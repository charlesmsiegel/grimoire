"""Raw-scan previews of removing or updating one source (079).

Every canonical file outside the changed source is read as JSON and walked; any string that
parses as a definition or image ref of that source, and any bare source id under a ``source`` key,
is a reference. Like 042's history scan, this is raw, so a record shape this version does not know
still counts. Each reference is then resolved by its exact qualified id on both sides of the
change. Nothing is matched by name, slug, list position or a lookalike source id.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from grimoire.core.identity import ImageRef, SourceRef, validate_source_id
from grimoire.core.index import MissingDefinition
from grimoire.store import CanonicalScan, LibraryStore, LibraryView, StoreError, StoredDefinition, etag
from grimoire.store.jsonfmt import MAX_DEPTH as JSON_MAX_DEPTH
from grimoire.store.jsonfmt import JsonError, parse_strict
from grimoire.store.layout import SOURCE_DIR, SOURCES, classify, image_parts

from .dependencies import source_version
from .heads import entity_heads
from .model import DEPENDENCIES_KEY, EntityChange, Problem, Reference, SourceChangePreview, SourceUpgradeError

#: nesting the scan follows: the deepest JSON the store accepts; deeper is reported, not searched
MAX_DEPTH = JSON_MAX_DEPTH + 1


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
        """Each entity of the source (unpinned) to its head (revision, name) as the loaded index
        resolves it, a declared head or, where the manifest declares none, the index's choice."""
        if self.view is None or self.source not in self.view.sources:
            return {}
        return {ref: (stored.ref.revision, stored.name) for ref, stored in entity_heads(self.view, self.source).items()}

    @property
    def loaded(self) -> bool:
        return self.view is not None and self.source in self.view.sources

    @property
    def version(self) -> str | None:
        """The source's ``source_version`` (what a dependency pin compares), ``None`` unless loaded."""
        return source_version(self.view, self.source) if self.view is not None else None

    def file_etag(self, path: str) -> str | None:
        """A file of the source (an image) by etag; nothing resolves while the source is not loaded."""
        data = self.files.get(path) if self.loaded else None
        return None if data is None else etag(data)


def _owner(path: str) -> str | None:
    """The source a canonical path belongs to, or ``None`` for a campaign file."""
    parts = path.split('/')
    if parts[0] != SOURCES or SOURCE_DIR not in parts:
        return None
    return ':'.join(parts[1:parts.index(SOURCE_DIR)])


def _blocks(path: str, source: str) -> bool:
    """Whether a blocked ``path`` hides some of ``source``: inside it, or a folder above its ``_source``."""
    home = '/'.join((SOURCES, *source.split(':'), SOURCE_DIR))
    return _owner(path) == source or home.startswith(path + '/')


def _record(path: str) -> str:
    try:
        found = classify(tuple(path.split('/')))
    except ValueError:
        found = None
    return 'unknown' if found is None else found[0]


#: key paths (list positions dropped) whose unpinned refs name an entity without reaching its content
ART_LAYER = ('art_pin', 'layers', 'entity')
EXCLUSIONS = ('payload', 'exclusions')
CATALOG_SCHEMA = 'grimoire.catalog/1'


def _unpinned(text: str, source: str) -> SourceRef | None:
    try:
        ref = SourceRef.parse(text)
    except ValueError:
        return None
    return ref if ref.source == source and not ref.pinned else None


def _references(payload: object, source: str) -> tuple[set[tuple[str, str]], bool]:
    """``(kind, text)`` for every string naming ``source`` (``definition``, ``image``, ``source``,
    ``dependency`` or ``identity``), and whether a container past ``MAX_DEPTH`` went unsearched.

    An unpinned ref at an identity-only key path (an art pin's layer entity beside its pinned base, a
    catalog's exclusions) is kind ``identity``: it names the entity, reaches no content and follows
    nothing; the same text used elsewhere in the file as content wins. An unpinned ref beside a
    ``resolved_revision`` (a head-following chat binding, 107) also means that revision, so the pinned
    ref at it is a reference too; once the binding is detached (``detached_seq``) it is provenance only
    and no longer follows the head. A bare source id under ``source`` in a top-level
    ``optional_dependencies`` list is a ``dependency`` declaration, not a world attachment."""
    found: set[tuple[str, str]] = set()
    deep = False
    identity = {ART_LAYER}
    if isinstance(payload, Mapping) and payload.get('schema') == CATALOG_SCHEMA:
        identity.add(EXCLUSIONS)
    stack: list[tuple[object, int, tuple[str, ...]]] = [(payload, 0, ())]
    while stack:
        value, depth, keys = stack.pop()
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
                found.add(('identity' if not ref.pinned and keys in identity else 'definition', value))
        elif isinstance(value, (Mapping, list, tuple)) and depth >= MAX_DEPTH:
            deep = True
        elif isinstance(value, Mapping):
            if value.get('source') == source:
                found.add(('dependency' if keys == (DEPENDENCIES_KEY,) else 'source', source))
            revision = value.get('resolved_revision')
            detached = value.get('detached_seq') is not None
            for key, item in value.items():
                stack.append((key, depth + 1, keys))
                meant = _unpinned(item, source) if isinstance(revision, str) and isinstance(item, str) else None
                if meant is not None:
                    try:
                        found.add(('definition', str(meant.at(revision))))
                    except ValueError:  # a hand-edited revision no pin can name
                        pass
                    if detached:  # provenance: the detached binding no longer reaches its head
                        continue
                stack.append((item, depth + 1, (*keys, key) if isinstance(key, str) else keys))
        elif isinstance(value, (list, tuple)):
            stack.extend((item, depth + 1, keys) for item in value)
    content = {text for kind, text in found if kind == 'definition'}
    return {(kind, text) for kind, text in found if not (kind == 'identity' and text in content)}, deep


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
    # A blocked path inside the changed source is a problem of its before side, not a skipped referrer.
    problems = [Problem(path, reason, 'before' if _owner(path) == source else 'library')
                for path, reason in sorted(scan.blocked.items())]
    for side, label in ((old, 'before'), (new, 'after')):
        if side.view is not None:
            problems.extend(Problem(problem.path, problem.code, label)
                            for problem in side.view.problems if problem.source == source)
        if label == 'after' and side.scan is not None:
            problems.extend(Problem(path, reason, label) for path, reason in sorted(side.scan.blocked.items())
                            if _blocks(path, source))
    rows: dict[tuple[str, str, str], Reference] = {}
    for path, data in sorted(scan.files.items()):
        if _owner(path) == source:
            continue
        try:
            payload = parse_strict(data)
        except JsonError:
            problems.append(Problem(path, 'unreadable'))
            continue
        record = _record(path)
        found, deep = _references(payload, source)
        if deep:
            problems.append(Problem(path, 'too_deep'))
        for kind, text in sorted(found):
            if kind == 'definition':
                row = _definition_row(path, record, text, old, new)
            elif kind == 'identity':  # existence only: never a follow
                ref = SourceRef.parse(text)
                was, now = old.definition(ref), new.definition(ref)
                row = _etag_row(path, record, text, was and was.ref.revision, now and now.ref.revision, 'unchanged')
                row = replace(row, status='unchanged') if was is not None and now is not None else row
            elif kind == 'image':
                try:
                    where: str | None = '/'.join(image_parts(ImageRef.parse(text)))
                except (StoreError, ValueError):  # an id no file can hold (``con``): it reaches nothing
                    where = None
                row = _etag_row(path, record, text, where and old.file_etag(where), where and new.file_etag(where),
                                'edited')
            elif kind == 'dependency':  # a pin compares the version digest, not the manifest's bytes
                row = _etag_row(path, record, text, old.version, new.version, 'changed')
            else:
                row = _etag_row(path, record, text, old.manifest_etag, new.manifest_etag, 'changed')
            rows[(path, kind, text)] = row
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
    unique = tuple(dict.fromkeys(problems))  # a load problem and a scan block can name one file
    return SourceChangePreview(source, action, old.manifest_etag, new.manifest_etag, tuple(entities),
                               tuple(edited), tuple(rows[key] for key in sorted(rows)), unique)


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
    side, scan = _scan_side(candidate, source)
    if not side.files and not any(_blocks(path, source) for path in scan.blocked):
        raise SourceUpgradeError('candidate_missing_source', source)
    # A candidate that holds the source but cannot load it (a malformed manifest) is previewed with
    # nothing resolving after, and its load problems are reported on the ``after`` side.
    return _preview(store, source, side, 'update')
