"""Disk facts for one validation pass or read (Sprint 018, 046): the ``_Disk`` cache over a ``LibraryStore``.

Split out of ``grimoire.imagestore.library`` so the batch and the resolver share one reader.
"""

from __future__ import annotations

from weakref import WeakKeyDictionary

from grimoire.core.identity import ImageRef, InstanceRef, PlacementRef, SourceRef, paired_instances
from grimoire.images import (
    IMAGE_SCHEMA, PLACEMENT_SCHEMA, BlobFact, ImageEntry, ImageModelError, PlacementEntry, Problem, image_from_payload,
    placement_from_payload,
)
from grimoire.store import (
    BlobCorrupt, LibraryStore, LibraryView, LinkRefused, MalformedRecord, RecordEnvelope, RecordNotFound, SourceFacts, StoredAssetRecord, StoredDefinition,
    StoredRecord, StoredSource, StoreError, UnstorableIdentifier, UnsupportedFormat,
)
from grimoire.store.layout import address_parts, classify
from grimoire.store.replica import instance_derivation

from .lineage import Lineage, lineage

BlobKey = tuple[str, str]  # (sha256, ext)


class InvalidImage(ValueError):
    """An image or blob operation refused; nothing was staged. ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


# -- disk facts (one cache per validation pass or read) ----------------------------------------

def _unstorable_is_missing(check):  # type: ignore[no-untyped-def]
    """A reference that parses but no portable path can hold (``world:con``) names nothing."""
    try:
        return check()
    except UnstorableIdentifier:
        return "missing_source"


#: per store, the last library load and the ``SourceFacts`` it was read under (046 lineage)
_PASS_VIEWS: WeakKeyDictionary[LibraryStore, tuple[SourceFacts, LibraryView]] = WeakKeyDictionary()


class _Disk:
    """Facts read from the store, cached for one pass; the store lock is held by the caller.

    Every one-source check shares one verified ``SourceFacts``, so validating many subjects costs
    one verification and then no rescans of an unchanged source.
    """

    def __init__(self, store: LibraryStore) -> None:
        self.store = store
        self.generation = store.generation
        self._cache: dict[tuple, object] = {}

    def _memo(self, key: tuple, compute):  # type: ignore[no-untyped-def]
        if key not in self._cache:
            self._cache[key] = compute()
        return self._cache[key]

    def facts(self) -> SourceFacts:
        return self._memo(("facts",), self.store.source_facts)  # type: ignore[no-any-return]

    def source(self, source_id: str) -> StoredSource | str:
        return self._memo(("source", source_id), lambda: _unstorable_is_missing(
            lambda: self.store.check_source(source_id, self.facts())))

    def definition(self, ref: SourceRef) -> StoredDefinition | str:
        return self._memo(("definition", ref), lambda: _unstorable_is_missing(
            lambda: self.store.check_definition(ref, self.facts())))

    def campaign_exists(self, campaign: str) -> bool:
        return self._memo(("campaign", campaign), lambda: self.store.campaign_exists(campaign))

    def view(self) -> LibraryView:
        """One library load (046): only a derived head's lineage needs it. Inside one
        ``store.read_pass()`` the verified ``SourceFacts`` stay the same object until a commit, so every
        resolution in the pass shares one load, exactly as it shares one verification."""
        def shared() -> LibraryView:
            facts = self.facts()
            cached = _PASS_VIEWS.get(self.store)
            if cached is not None and cached[0] is facts:
                return cached[1]
            loaded = self.store.load()
            _PASS_VIEWS[self.store] = (facts, loaded)
            return loaded
        return self._memo(("view",), shared)  # type: ignore[no-any-return]

    def lineage(self, entity: SourceRef) -> Lineage:
        return self._memo(("lineage", entity), lambda: lineage(entity, self.definition, self.view))

    def paired(self, campaign: str) -> frozenset[InstanceRef]:
        """The campaign's instances on disk whose id another kind also uses (``paired_instances``, the
        one definition). Only disk counts: the store itself refuses a batch ``put`` that would form a
        pair (``InstanceIdConflict``), so a batch can never commit one of its own."""
        return self._memo(("paired", campaign), lambda: paired_instances(self.store.campaign_instances(campaign)))

    def instance(self, ref: InstanceRef) -> StoredRecord | str:
        def compute() -> StoredRecord | str:
            try:
                return self.store.read(ref)  # type: ignore[return-value]
            except RecordNotFound:
                return "missing"
            except UnsupportedFormat:
                return "unsupported_format"
            except (StoreError, ValueError, OSError):
                return "unreadable"
        return self._memo(("instance", ref), compute)

    def image(self, ref: ImageRef) -> ImageEntry:
        def compute() -> ImageEntry:
            if ref.source is not None and not isinstance(self.source(ref.source), StoredSource):
                return ImageEntry(ref, None, "missing_image")  # an excluded source shows nothing
            try:
                stored = self.store.read(ref)
            except RecordNotFound:
                return ImageEntry(ref, None, "missing_image")
            except UnsupportedFormat:
                return ImageEntry(ref, None, "unsupported_format")
            except (StoreError, ValueError, OSError):
                return ImageEntry(ref, None, "invalid_image")
            record = stored.record  # type: ignore[union-attr]
            if record.schema != IMAGE_SCHEMA:
                return ImageEntry(ref, None, "invalid_image")
            try:
                return ImageEntry(ref, image_from_payload(record.payload))
            except ImageModelError:
                return ImageEntry(ref, None, "invalid_image")
        return self._memo(("image", ref), compute)

    def image_present(self, ref: ImageRef) -> bool:
        """Whether any file occupies the image's name (readable or not): ids are never reused."""
        def compute() -> bool:
            try:
                self.store.read(ref)
            except RecordNotFound:
                return False
            except MalformedRecord:  # a file is there (unreadable, newer or misaddressed): occupied
                return True
            except (StoreError, ValueError, OSError) as error:  # a link or damaged folder, not a duplicate
                raise InvalidImage(f"cannot tell whether {ref} exists: {error}", "unreadable_scope") from None
            return True
        return self._memo(("image_present", ref), compute)

    def blob(self, key: BlobKey) -> tuple[str, int | None]:
        """``(state, size)``: ``absent``, ``intact``, ``corrupt``, ``oversize`` or ``lookalike``."""
        def compute() -> tuple[str, int | None]:
            presence, size = self.store.blob_stat(*key)
            if presence != "present":
                return presence, None
            try:
                current = self.store.blob_etag(*key)
            except BlobCorrupt:
                return "oversize", size
            return ("intact" if current == "sha256:" + key[0] else "corrupt"), size
        return self._memo(("blob", key), compute)

    def blob_etag(self, key: BlobKey) -> str | None:
        return self._memo(("blob_etag", key), lambda: self.store.blob_etag(*key))

    def blob_fact(self, key: BlobKey) -> BlobFact:
        def compute() -> BlobFact:
            try:
                presence, size = self.store.blob_stat(*key)
            except (LinkRefused, UnstorableIdentifier, StoreError, ValueError, OSError):
                return BlobFact("absent")
            return BlobFact(presence, size)
        return self._memo(("blob_fact", key), compute)

    def placements(self, subject: SourceRef | InstanceRef) -> tuple[dict[str, PlacementEntry], tuple[Problem, ...]]:
        """One subject's placement entries, plus problems; see ``layer`` for the fail-closed view."""
        entries, problems, _ = self.layer(subject)
        return entries, problems

    def layer(self, subject: SourceRef | InstanceRef,
              ) -> tuple[dict[str, PlacementEntry], tuple[Problem, ...], bool]:
        """``(entries, problems, trusted)``.

        A problem file whose path is one of this subject's placements (a link, a directory) becomes
        an ``invalid_placement`` entry for its slot. A linked, unreadable or look-alike folder, or a
        look-alike file name, makes the whole layer untrusted: it might be hiding something.
        """
        def compute() -> tuple[dict[str, PlacementEntry], tuple[Problem, ...], bool]:
            try:
                refs, listed = self.store.list_placements(subject)
            except (StoreError, ValueError, OSError) as error:  # e.g. an id no portable path can hold
                return {}, (Problem(str(subject), "unreadable", str(error)),), False
            entries = {ref.slot: self._entry(ref) for ref in refs}
            problems, trusted = [], True
            folder = "/".join(address_parts(PlacementRef(subject, "x"))[:-1])
            for problem in listed:
                inside = problem.path.startswith(folder + "/")
                if problem.path == folder or folder.startswith(problem.path + "/"):
                    trusted = False  # the subject's own folder (or an ancestor) is damaged
                elif not inside and problem.code == "name_mismatch":
                    trusted = False  # a look-alike folder beside the canonical one may hold its records
                elif inside and problem.path.casefold().endswith(".json") and problem.code != "link":
                    trusted = False  # any non-canonical .json (``avatar copy.json``) may be a placement
                problems.append(Problem(problem.path, problem.code, problem.detail))
                hit = classify(tuple(problem.path.split("/")))
                if hit is not None and hit[0] == "placement" and hit[1].subject == subject:  # type: ignore[union-attr]
                    ref = hit[1]
                    entries[ref.slot] = PlacementEntry(ref, None, "invalid_placement", problem.path)  # type: ignore[union-attr]
                elif problem.code in ("link", "unreadable"):
                    trusted = False
            return entries, tuple(problems), trusted
        return self._memo(("placements", subject), compute)

    def _entry(self, ref: PlacementRef) -> PlacementEntry:
        text = "/".join(address_parts(ref))
        try:
            stored = self.store.read(ref)
        except UnsupportedFormat:
            return PlacementEntry(ref, None, "unsupported_format", text)
        except (StoreError, ValueError, OSError):
            return PlacementEntry(ref, None, "invalid_placement", text)
        record = stored.record  # type: ignore[union-attr]
        if record.schema != PLACEMENT_SCHEMA:
            return PlacementEntry(ref, None, "invalid_placement", text)
        try:
            return PlacementEntry(ref, placement_from_payload(record.payload), None, text)
        except ImageModelError:
            return PlacementEntry(ref, None, "invalid_placement", text)

    def current_placement(self, ref: PlacementRef) -> StoredAssetRecord | str:
        def compute() -> StoredAssetRecord | str:
            try:
                return self.store.read(ref)  # type: ignore[return-value]
            except RecordNotFound:
                return "missing"
            except UnsupportedFormat:
                return "unsupported_format"
            except (StoreError, ValueError, OSError):
                return "malformed"
        return self._memo(("current", ref), compute)


def derivation(envelope: RecordEnvelope) -> tuple[SourceRef | None, str | None]:
    """The unpinned entity an instance derives from (``derived_from``, else ``base``) or a problem code.
    One rule with sync's (``replica.instance_derivation``), so the two never disagree about W7."""
    return instance_derivation(envelope.derived_from, envelope.base)
