"""Store-backed pictures (Sprint 018): add images, place them, repair blobs, resolve what shows.

``ImageLibrary`` is trusted application code and is never handed to a worker. Every write is an
``ImageBatch``: images, placements, blob repairs and an importer's own definitions, instances,
manifests and play-scope or chat records (026), committed as **one** guarded store transaction a human can ``preview`` and approve. Each op is
validated when added, against disk plus the batch's earlier ops; ``commit`` re-validates the
whole batch under the store lock before building the transaction, so nothing decided on stale
facts is written.

A placement may not name an instance whose id another kind of its campaign also uses (a pair,
IDENTITY.md): ``load()`` leaves both members out, so the id is no one individual to picture. Only a
``hide`` may still be written there, the one way to take art off a slot, so a human can undo what was
placed before the pair formed.

Reads fail closed: a source that ``load()`` would exclude, a dropped entity or a broken
reference shows nothing, never another picture. No model call and no network fetch happen here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

from grimoire.core.identity import (
    ChatRef, ImageRef, InstanceRef, PlacementRef, ScopeRef, SourceRef, TranscriptRef, paired_instances,
)
from grimoire.images import (
    IMAGE_SCHEMA, PLACEMENT_SCHEMA, BlobFact, BlobInfo, Facts, ImageEntry, ImageModelError, ImageObject, Placement,
    Layer, PlacementEntry, Problem, Resolution, Shown, Slot, image_from_payload, image_to_payload,
    placement_from_payload, placement_to_payload, resolve_lineage, validate_placement,
)
from grimoire.store import (
    AssetRecord, BlobCorrupt, ImageBlob, InvalidBlob, LibraryStore, LinkRefused, MalformedRecord, NameCollision,
    LibraryView, RecordEnvelope, RecordNotFound, ScopeRecord, SourceManifest, StaleWrite, StoredAssetRecord, StoredDefinition,
    StoredRecord, SourceFacts, StoredSource, StoreError, TakenIdsUnlisted, UnstorableIdentifier, UnsupportedFormat,
)
from grimoire.store.layout import address_parts, blob_parts, classify, manifest_parts
from grimoire.store.replica import instance_derivation

from .lineage import Lineage, is_derived, lineage

BlobKey = tuple[str, str]  # (sha256, ext)


def placement_subject_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    """Every instance id with a campaign placement folder (``placements/<kind>/<id>/``), from listings only.

    Part of the one reservation set (``grimoire.chatstore.campaign_taken_ids``): a hand-deleted
    actor's id must never go to a different individual, who would show the old one's ``hide`` or
    ``set`` art. No placement file is read. A look-alike folder keeps the id it folds to taken. A
    folder that could hold one but is a link or cannot be listed (or a look-alike campaign folder)
    raises ``TakenIdsUnlisted``: allocation fails closed rather than guess.
    """
    refs, complete = store.campaign_placement_subjects(campaign_id)
    if not complete:
        folder = f"campaigns/{campaign_id}/placements"
        raise TakenIdsUnlisted(f"{folder} cannot be fully listed (a linked or unlistable folder, or a look-alike "
                               "campaign folder), so placement subject ids cannot be reserved", (folder,))
    return frozenset(ref.instance_id for ref in refs)


class InvalidImage(ValueError):
    """An image or blob operation refused; nothing was staged. ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


class InvalidBatch(ValueError):
    """A batch op refused that is neither an image nor a placement (a repeated ``put``)."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


class InvalidPlacement(ValueError):
    """A placement refused; nothing was staged. ``reason`` is machine-readable.

    ``etag`` is set when ``read_placement`` refuses a file this version cannot interpret, so a
    caller can still replace it by compare-and-swap.
    """

    def __init__(self, message: str, reason: str, etag: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.etag = etag

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason, self.etag))


@dataclass(frozen=True)
class ImageCommit:
    etags: Mapping[str, str]
    images: tuple[ImageRef, ...]
    placements: tuple[PlacementRef, ...]


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


# -- the batch ---------------------------------------------------------------------------------

@dataclass
class _Placed:
    placement: Placement
    expected: str | None
    owner: str | None
    extra: Mapping[str, object]


@dataclass
class _State:
    """Disk plus the batch so far: what each later op is validated against."""

    manifests: set[str] = field(default_factory=set)
    definitions: set[tuple[str, str, str]] = field(default_factory=set)
    instances: dict[InstanceRef, RecordEnvelope] = field(default_factory=dict)
    campaigns: set[str] = field(default_factory=set)
    images: dict[ImageRef, ImageObject] = field(default_factory=dict)
    blobs: dict[BlobKey, ImageBlob] = field(default_factory=dict)  # added by add_image
    repairs: dict[BlobKey, tuple[ImageBlob, str]] = field(default_factory=dict)  # blob, actual etag
    placements: dict[PlacementRef, _Placed] = field(default_factory=dict)
    puts: set[tuple] = field(default_factory=set)
    #: manifests and definition revisions put in this batch (046): they decide whose head is derived
    staged_manifests: dict[str, SourceManifest] = field(default_factory=dict)
    staged_definitions: dict[SourceRef, RecordEnvelope] = field(default_factory=dict)

    def copy(self) -> _State:
        return _State(set(self.manifests), set(self.definitions), dict(self.instances), set(self.campaigns),
                      dict(self.images), dict(self.blobs), dict(self.repairs), dict(self.placements),
                      set(self.puts), dict(self.staged_manifests), dict(self.staged_definitions))


class ImageBatch:
    """Images, placements, repairs and records to write together; ``preview`` then ``commit``."""

    def __init__(self, library: ImageLibrary) -> None:
        self.library = library
        self._ops: list[tuple] = []
        self._state = _State()
        self._disk = _Disk(library.store)
        self._preview: dict[str, bytes] | None = None
        self._committed = False

    # -- building --------------------------------------------------------------------------
    def _add(self, op: tuple) -> object:
        if self._committed:
            raise StoreError("this batch was already committed")
        state = self._state.copy()
        if self._disk.generation != self.library.store.generation:  # a commit since: facts expired
            self._disk = _Disk(self.library.store)
        with self.library.store.read_pass():  # the lock, and exclusions verified once
            result = _apply(self.library, state, self._disk, op)
        self._state = state
        self._ops.append(op)
        return result

    def put(self, envelope: RecordEnvelope | SourceManifest | ScopeRecord, expected: str | None) -> None:
        """An importer's own definition, instance, manifest, play scope, chat or transcript page (036), in the
        same transaction.

        A play scope or chat record makes its campaign exist for the batch's later ops (W5); a transcript
        page does not."""
        if isinstance(envelope, ScopeRecord):
            if not isinstance(envelope.address, (ScopeRef, ChatRef, TranscriptRef)):
                raise TypeError("an image batch puts only play-scope, chat and transcript scope records")
        elif not isinstance(envelope, (RecordEnvelope, SourceManifest)):
            raise TypeError("an image batch puts only RecordEnvelopes, SourceManifests and scope or chat records")
        # an address no portable path can hold raises UnstorableIdentifier now, never at commit
        manifest_parts(envelope.source) if isinstance(envelope, SourceManifest) else address_parts(envelope.address)
        self._add(("put", envelope, expected))

    def add_image(self, scope: str, image_id: str, data: bytes,
                  provenance: Mapping[str, object] | None = None) -> ImageRef:
        return self._add(("image", scope, image_id, data, provenance))  # type: ignore[return-value]

    def repair_blob(self, data: bytes) -> BlobKey:
        return self._add(("repair", data))  # type: ignore[return-value]

    def place(self, subject: SourceRef | InstanceRef, slot: str, placement: Placement,
              expected: str | None) -> PlacementRef:
        return self._add(("place", subject, slot, placement, expected))  # type: ignore[return-value]

    # -- committing --------------------------------------------------------------------------
    def _build(self) -> tuple[list[tuple[object, str | None]], _State, _Disk]:
        """Re-validate every op against fresh disk facts and list the store puts (lock held).

        A pair formed since a placement was added (another kind took its subject's id, by a hand
        edit or a sync) is ``StaleWrite``, checked before the ops are replayed so it is never
        mistaken for a refusal of the batch as built: the batch was valid, the library moved on.
        """
        disk, state = _Disk(self.library.store), _State()
        paired = sorted({"/".join(address_parts(op[1])) for op in self._ops
                         if op[0] == "place" and _names_paired(disk, op[1], op[3])})
        if paired:
            raise StaleWrite([(path, "instance_id_conflict") for path in paired])
        for op in self._ops:
            _apply(self.library, state, disk, op)
        for ref, placed in state.placements.items():  # against the finished batch, not just earlier ops
            _check_placement(state, disk, ref, placed.placement)
        library = self.library
        puts: list[tuple[object, str | None]] = []
        for key, (blob, _) in state.repairs.items():
            puts.append((blob, disk.blob_etag(key)))  # the repair survives dedup
        for key, blob in state.blobs.items():
            if key in state.repairs:
                continue
            found, _ = disk.blob(key)
            if found == "intact":
                continue  # deduplicated: identical bytes already stored
            puts.append((blob, None))  # absent (a look-alike name is refused by the store)
        for op in self._ops:
            if op[0] == "put":
                puts.append((op[1], op[2]))
        for ref, image in state.images.items():
            puts.append((AssetRecord(ref, IMAGE_SCHEMA, image_to_payload(image), owner=library.owner), None))
        for ref, placed in state.placements.items():
            record = AssetRecord(ref, PLACEMENT_SCHEMA, placement_to_payload(placed.placement),
                                 owner=placed.owner, extra=placed.extra)
            puts.append((record, placed.expected))
        return puts, state, disk

    def _transaction(self, puts):  # type: ignore[no-untyped-def]
        transaction = self.library.store.transaction()
        for envelope, expected in puts:
            transaction.put(envelope, expected)
        return transaction

    def preview(self) -> dict[str, bytes]:
        """The exact bytes each target will hold, keyed by logical path: what a human approves."""
        with self.library.store.read_pass():
            puts, _, _ = self._build()
            self._preview = self._transaction(puts).preview() if puts else {}
        return dict(self._preview)

    def check(self) -> None:
        """Run the commit's own checks on the batch as built, read-only: raises what ``commit`` would
        raise before authorizing (a name collision, a source that cannot hold the writes). Consumes no
        approval and writes nothing."""
        with self.library.store.read_pass():
            if self._committed:
                raise StoreError("this batch was already committed")
            puts, _, _ = self._build()
            if puts:
                self._transaction(puts).check()

    def commit(self, approvals: Mapping[str, str] | None = None) -> ImageCommit:
        store = self.library.store
        with store.read_pass():  # the lock: nothing can change between these checks and the commit
            if self._committed:
                raise StoreError("this batch was already committed")
            puts, state, disk = self._build()
            transaction = self._transaction(puts) if puts else None
            rendered = transaction.preview() if transaction is not None else {}
            if self._preview is not None:
                _check_preview(self._preview, rendered, state, disk)
            result = transaction.commit(self.library.handle, approvals) if transaction is not None else None
            self._committed = True
        return ImageCommit(result.etags if result is not None else {}, tuple(state.images),
                           tuple(state.placements))


def _check_preview(previewed: Mapping[str, bytes], rendered: Mapping[str, bytes], state: _State, disk: _Disk) -> None:
    """Only dropping a blob that is now present and intact may differ from what a human saw."""
    changed = [path for path in set(previewed) | set(rendered) if previewed.get(path) != rendered.get(path)]
    allowed = {"/".join(blob_parts(*key)) for key in state.blobs if disk.blob(key)[0] == "intact"}
    stale = [path for path in changed if not (path in allowed and path not in rendered)]
    if stale:
        raise StaleWrite([(path, "changed since preview") for path in sorted(stale)])


# -- validation (W1-W11) -----------------------------------------------------------------------

def _admitted(state: _State, disk: _Disk, source_id: str) -> bool:
    status = disk.source(source_id)
    return isinstance(status, StoredSource) or (status == "missing_source" and source_id in state.manifests)


def _apply(library: ImageLibrary, state: _State, disk: _Disk, op: tuple) -> object:
    kind = op[0]
    if kind == "put":
        envelope = op[1]
        key = ("manifest", envelope.source) if isinstance(envelope, SourceManifest) else ("record", envelope.address)
        if key in state.puts:
            raise InvalidBatch(f"{key[1]} is put twice in one batch", "duplicate_put")
        state.puts.add(key)
        if isinstance(envelope, SourceManifest):
            state.manifests.add(envelope.source)
            state.staged_manifests[envelope.source] = envelope
        elif isinstance(envelope, ScopeRecord):
            if not isinstance(envelope.address, TranscriptRef):
                state.campaigns.add(envelope.address.campaign_id)
        elif isinstance(envelope.address, SourceRef):
            state.definitions.add(envelope.address.entity_key)
            state.staged_definitions[envelope.address] = envelope
        else:
            state.instances[envelope.address] = envelope
            state.campaigns.add(envelope.address.campaign_id)
        return None
    if kind == "image":
        return _add_image(state, disk, *op[1:])
    if kind == "repair":
        return _repair(state, disk, op[1])
    return _place(library, state, disk, *op[1:])


def _blob_of(data: object, error: type[ValueError]) -> ImageBlob:
    try:
        return ImageBlob(data)  # type: ignore[arg-type]
    except InvalidBlob as caught:
        raise error(str(caught), caught.reason) from None


def _add_image(state: _State, disk: _Disk, scope: str, image_id: str, data: bytes,
               provenance: Mapping[str, object] | None) -> ImageRef:
    try:
        ref = ImageRef(scope, image_id)
    except ValueError as error:
        raise InvalidImage(str(error), "invalid_field") from None
    address_parts(ref)  # an id no portable path can hold raises UnstorableIdentifier, never "exists"
    blob = _blob_of(data, InvalidImage)  # W1, W2
    try:
        image = ImageObject(BlobInfo(blob.sha256, blob.media_type, blob.size), provenance)
    except ImageModelError as error:
        raise InvalidImage(str(error), "invalid_field") from None
    if ref in state.images or disk.image_present(ref):  # W3
        raise InvalidImage(f"{ref} exists; a different picture is a new image object", "image_exists")
    campaign = ref.campaign_id
    if campaign is not None:  # W5
        if campaign not in state.campaigns and not disk.campaign_exists(campaign):
            raise InvalidImage(f"campaign {campaign} does not exist", "missing_scope")
    elif not _admitted(state, disk, ref.scope):
        raise InvalidImage(f"source {ref.scope} is missing or excluded", "missing_scope")
    key = (blob.sha256, blob.ext)
    if key not in state.blobs and key not in state.repairs:  # W4
        found, _ = disk.blob(key)
        if found == "lookalike":
            raise NameCollision(f"{'/'.join(blob_parts(*key))} collides with a differently spelled existing entry")
        if found in ("corrupt", "oversize"):
            raise InvalidImage(f"blob {blob.sha256} is corrupt on disk; repair it first", "blob_corrupt")
    state.images[ref] = image
    state.blobs.setdefault(key, blob)
    return ref


def _repair(state: _State, disk: _Disk, data: bytes) -> BlobKey:
    blob = _blob_of(data, InvalidImage)
    key = (blob.sha256, blob.ext)
    found, _ = disk.blob(key)  # LinkRefused / UnstorableIdentifier propagate from the store
    if found == "lookalike":
        raise NameCollision(f"{'/'.join(blob_parts(*key))} collides with a differently spelled existing entry")
    if found == "oversize":
        raise InvalidImage(f"blob {blob.sha256} is larger than any blob may be; delete it by hand", "oversize_blob")
    if found != "corrupt" or key in state.repairs:
        raise InvalidImage(f"blob {blob.sha256} is {found}; nothing to repair", "nothing_to_repair")
    state.repairs[key] = (blob, disk.blob_etag(key))  # type: ignore[assignment]
    return key


def _subject_exists(state: _State, disk: _Disk, subject: SourceRef | InstanceRef) -> bool:
    if isinstance(subject, SourceRef):
        if isinstance(disk.definition(subject), StoredDefinition):
            return True
        return subject.entity_key in state.definitions and _admitted(state, disk, subject.source)
    return subject in state.instances or isinstance(disk.instance(subject), StoredRecord)


def _instance_derivation(state: _State, disk: _Disk, subject: InstanceRef) -> SourceRef | None:
    """The derivation entity, only if it exists (on disk or in the batch); otherwise ``None``,
    so only campaign images are in scope, exactly as the resolver will decide."""
    envelope = state.instances.get(subject)
    if envelope is None:
        stored = disk.instance(subject)
        if not isinstance(stored, StoredRecord):
            return None
        envelope = stored.envelope
    entity = derivation(envelope)[0]
    return entity if entity is not None and _subject_exists(state, disk, entity) else None


def _image_entry(state: _State, disk: _Disk, ref: ImageRef) -> ImageEntry:
    image = state.images.get(ref)
    return ImageEntry(ref, image) if image is not None else disk.image(ref)


def _blob_fact(state: _State, disk: _Disk, image: ImageObject) -> BlobFact:
    """Presence and size only (never the bytes); a link or non-regular entry counts as absent."""
    key = (image.blob.sha256, image.blob.ext)
    staged = state.blobs.get(key) or (state.repairs[key][0] if key in state.repairs else None)
    if staged is not None:
        return BlobFact("present", staged.size)
    return disk.blob_fact(key)


def _source_layer(state: _State, disk: _Disk, entity: SourceRef) -> tuple[dict[str, PlacementEntry], bool]:
    """``entity``'s placements on disk with this batch's over them, and whether its folder is trusted."""
    own, _, trusted = disk.layer(entity)
    layer = dict(own)
    for ref, placed in state.placements.items():
        if ref.subject == entity:
            layer[ref.slot] = PlacementEntry(ref, placed.placement, None, str(ref))
    return layer, trusted


def _lineage(state: _State, disk: _Disk, entity: SourceRef) -> Lineage:
    """``entity``'s art lineage as this batch would leave it. When the batch puts a manifest or a
    revision of any entity in it, only the entity's own layer is kept, derived exactly when the head
    the batch leaves has a ``derivation``: its staged base chain is not walked, so nothing is inherited
    (and nothing can be re-cropped) until it is committed, and the batch never writes a mode its own
    reader would refuse."""
    found = disk.lineage(entity)
    if not any(_staged(state, member) for member in found.entities) and not _staged(state, entity):
        return found
    return Lineage((entity,), (_head_is_derived(state, disk, entity),))


def _staged(state: _State, entity: SourceRef) -> bool:
    return entity.source in state.staged_manifests or any(
        ref.entity_key == entity.entity_key for ref in state.staged_definitions)


def _head_is_derived(state: _State, disk: _Disk, entity: SourceRef) -> bool:
    manifest = state.staged_manifests.get(entity.source)
    if manifest is None:
        stored = disk.source(entity.source)
        manifest = stored.manifest if isinstance(stored, StoredSource) else None
    head = manifest.heads.get(f"{entity.kind}/{entity.entity_id}") if manifest is not None else None
    staged = [ref for ref in state.staged_definitions if ref.entity_key == entity.entity_key]
    if head is not None:
        ref: SourceRef | None = entity.at(head)
    elif not staged:
        ref = entity  # no declared head: the store follows the only revision
    else:  # a staged revision is the head only if it will be the entity's only one
        ref = staged[0] if len(staged) == 1 and not isinstance(disk.definition(entity), StoredDefinition) else None
    if ref is None:
        return False
    envelope = state.staged_definitions.get(ref)
    if envelope is None:
        found = disk.definition(ref)
        envelope = found.envelope if isinstance(found, StoredDefinition) else None
    return envelope is not None and is_derived(envelope)


def _below(state: _State, disk: _Disk, entities: tuple[SourceRef, ...], derived: tuple[bool, ...],
           slot: str) -> Slot | None:
    """What ``slot`` shows through ``entities`` (root first), staged placements included: the picture
    an ``adjust`` layered above them must name (W9)."""
    if not entities:
        return None
    layers: list[Layer] = []
    for entity, flag in zip(entities, derived):  # guarded exactly as the reader guards them
        entries, trusted = _source_layer(state, disk, entity)
        layers.append(Layer(entity, _guarded(entity, entries, trusted, layers) if flag else entries,
                            entity.source, flag))
    return resolve_lineage(entities[-1], layers, _facts(state, disk, [layer.entries for layer in layers])).slot(slot)


def _facts(state: _State, disk: _Disk, layers: list[Mapping[str, PlacementEntry]]) -> Facts:
    images, blobs = {}, {}
    for layer in layers:
        for entry in layer.values():
            if entry.placement is None or entry.placement.image is None:
                continue
            found = _image_entry(state, disk, entry.placement.image)
            images[found.ref] = found
            if found.image is not None:
                blobs[(found.image.blob.sha256, found.image.blob.ext)] = _blob_fact(state, disk, found.image)
    return Facts(images, blobs)


def _place(library: ImageLibrary, state: _State, disk: _Disk, subject: SourceRef | InstanceRef, slot: str,
           placement: Placement, expected: str | None) -> PlacementRef:
    try:
        ref = PlacementRef(subject, slot)
    except ValueError as error:
        raise InvalidPlacement(str(error), "invalid_field") from None
    address_parts(ref)  # a slot no portable path can hold (``con``) raises UnstorableIdentifier now
    if not isinstance(placement, Placement):
        raise InvalidPlacement("place takes a Placement", "invalid_field")
    derived = isinstance(subject, SourceRef) and _lineage(state, disk, subject).derived[-1]
    problem = validate_placement(subject, slot, placement, derived=derived)  # W8
    if problem is not None:
        raise InvalidPlacement(problem[1], problem[0])
    if ref in state.placements:
        raise InvalidPlacement(f"{ref} is placed twice in one batch", "invalid_field")
    if _names_paired(disk, subject, placement):  # W11
        raise InvalidPlacement(f"{subject}'s id is also used by another kind in its campaign", "instance_id_conflict")
    _check_placement(state, disk, ref, placement)
    owner, extra = library.owner, {}
    if expected is not None:  # carry what this version does not know forward (F13)
        current = disk.current_placement(ref)
        if isinstance(current, StoredAssetRecord):
            owner = current.record.owner or owner
            extra = dict(current.record.extra)
    state.placements[ref] = _Placed(placement, expected, owner, extra)
    return ref


def _names_paired(disk: _Disk, subject: SourceRef | InstanceRef, placement: Placement) -> bool:
    """Whether this placement would picture a paired instance (W11), which ``load()`` leaves out.

    A ``hide`` is exempt: it shows nothing, grants nothing, and is the only way 018 takes art off a
    slot (no op deletes a placement), so it is the repair a human needs to undo a ``set`` or
    ``adjust`` written before the pair formed, exactly as 017's ``end`` and 021's revocation stay
    allowed on a pair. An ``unresolved`` placement (038) is exempt for the same reason: it names no
    picture and shows nothing. An ``inherit`` (046) is not exempt: it can put the source's picture back.
    A definition subject is never in a pair: pairs are campaign instances only.
    """
    return (isinstance(subject, InstanceRef) and placement.mode not in ("hide", "unresolved")
            and subject in disk.paired(subject.campaign_id))


def _check_placement(state: _State, disk: _Disk, ref: PlacementRef, placement: Placement) -> None:
    """W5-W9 against disk plus ``state``; ``_build`` re-runs it for every placement once the whole
    batch is applied, so a later op cannot leave an earlier placement invalid."""
    subject, slot = ref.subject, ref.slot
    if not _subject_exists(state, disk, subject):  # W5
        raise InvalidPlacement(f"{subject} does not exist", "missing_subject")
    if isinstance(subject, SourceRef):  # W8 again: a later put may change whose head is derived (046)
        problem = validate_placement(subject, slot, placement, derived=_lineage(state, disk, subject).derived[-1])
        if problem is not None:
            raise InvalidPlacement(problem[1], problem[0])
    entity = subject if isinstance(subject, SourceRef) else _instance_derivation(state, disk, subject)
    if isinstance(subject, SourceRef):
        allowed = {subject.source}
    else:
        allowed = {f"campaign:{subject.campaign_id}"} | ({entity.source} if entity is not None else set())
    image = placement.image
    if image is not None:
        if placement.mode == "adjust":  # it names the inherited picture, owned anywhere in the lineage (046)
            beneath = _lineage(state, disk, entity) if entity is not None else None
            allowed |= {owner.source for owner in beneath.entities} if beneath is not None else set()
        if image.scope not in allowed:  # W7
            raise InvalidPlacement(f"{image} is not in scope for {subject}", "wrong_scope")
        found = _image_entry(state, disk, image)  # W6
        if found.image is None:
            reason = "missing_image" if found.reason in (None, "missing_image") else "invalid_image"
            raise InvalidPlacement(f"{image}: {found.reason or 'missing'}", reason)
        fact = _blob_fact(state, disk, found.image)
        if fact.presence != "present" or fact.size != found.image.blob.size:
            raise InvalidPlacement(f"the blob of {image} is missing or the wrong size", "missing_blob")
    if placement.mode == "adjust":  # W9, through the lineage beneath the subject (046)
        base = None
        if isinstance(subject, SourceRef):
            found = _lineage(state, disk, subject)
            base = _below(state, disk, found.entities[:-1], found.derived[:-1], slot)
        elif entity is not None:
            found = _lineage(state, disk, entity)
            base = _below(state, disk, found.entities, found.derived, slot)
        if not isinstance(base, Shown):
            raise InvalidPlacement(f"{subject} inherits no picture in {slot!r} to adjust", "nothing_to_adjust")
        if base.image != image:
            raise InvalidPlacement(f"{slot!r} inherits {base.image}, not {image}", "stale_adjust")


# -- the library -------------------------------------------------------------------------------

class ImageLibrary:
    """Pictures over one open, writable ``LibraryStore``."""

    def __init__(self, store: LibraryStore, handle: object, *, owner: str | None = None) -> None:
        self.store = store
        self.handle = handle
        self.owner = owner

    def change(self) -> ImageBatch:
        return ImageBatch(self)

    def add_image(self, scope: str, image_id: str, data: bytes,
                  provenance: Mapping[str, object] | None = None) -> ImageBatch:
        batch = self.change()
        batch.add_image(scope, image_id, data, provenance)
        return batch

    def place(self, subject: SourceRef | InstanceRef, slot: str, placement: Placement,
              expected: str | None = None) -> ImageBatch:
        batch = self.change()
        batch.place(subject, slot, placement, expected)
        return batch

    def repair_blob(self, data: bytes) -> ImageBatch:
        batch = self.change()
        batch.repair_blob(data)
        return batch

    # -- reading -----------------------------------------------------------------------------
    def read_image(self, ref: ImageRef) -> tuple[ImageObject, str]:
        """The image object and its etag; ``InvalidImage`` if this version cannot interpret it, or if
        its source would be excluded by ``load()`` (``excluded_source``)."""
        if ref.source is not None:
            status = _unstorable_is_missing(lambda: self.store.check_source(ref.source))
            if not isinstance(status, StoredSource):
                raise InvalidImage(f"{ref}: its source is {status}", str(status))
        try:
            stored = self.store.read(ref)
        except RecordNotFound:
            raise InvalidImage(f"{ref} does not exist", "missing_image") from None
        except UnsupportedFormat as error:
            raise InvalidImage(str(error), "unsupported_format") from None
        except (StoreError, ValueError, OSError) as error:  # malformed, misaddressed, a link, unreadable
            raise InvalidImage(str(error), "invalid_image") from None
        record = stored.record  # type: ignore[union-attr]
        if record.schema != IMAGE_SCHEMA:
            raise InvalidImage(f"{ref} has schema {record.schema!r}", "invalid_image")
        try:
            return image_from_payload(record.payload), stored.etag
        except ImageModelError as error:
            raise InvalidImage(str(error), "invalid_image") from None

    def read_placement(self, ref: PlacementRef) -> tuple[Placement, str]:
        """The placement and its etag, for a CAS rewrite that keeps its unknown fields.

        A malformed file, or one with an unknown schema or payload, raises ``InvalidPlacement``
        (``invalid_placement``) carrying its ``etag``, so it can still be replaced:
        ``place(subject, slot, new, expected=error.etag)``. A file written by a newer version raises
        ``unsupported_format`` with no etag: the store never overwrites it.
        """
        try:
            stored = self.store.read(ref)
        except UnsupportedFormat as error:  # written by a newer version: keep it, never replace it
            raise InvalidPlacement(str(error), "unsupported_format") from None
        except MalformedRecord as error:
            raise InvalidPlacement(str(error), "invalid_placement", error.etag) from None
        record = stored.record  # type: ignore[union-attr]
        if record.schema != PLACEMENT_SCHEMA:
            raise InvalidPlacement(f"{ref} has schema {record.schema!r}", "invalid_placement", stored.etag)
        try:
            return placement_from_payload(record.payload), stored.etag
        except ImageModelError as error:
            raise InvalidPlacement(str(error), "invalid_placement", stored.etag) from None

    def read_image_bytes(self, ref: ImageRef) -> bytes:
        """The picture's exact bytes, verified against the blob's name.

        ``BlobCorrupt`` if the bytes no longer match; ``InvalidImage`` for every other refusal
        (``missing_blob``, or the reasons ``read_image`` gives).
        """
        image, _ = self.read_image(ref)
        try:
            return self.store.read_blob(image.blob.sha256, image.blob.ext)
        except BlobCorrupt:
            raise
        except RecordNotFound:
            raise InvalidImage(f"the blob of {ref} is missing", "missing_blob") from None
        except (StoreError, ValueError, OSError) as error:
            raise InvalidImage(str(error), "missing_blob") from None

    def effective_placements(self, subject: SourceRef | InstanceRef) -> Resolution:
        """What ``subject`` shows in each slot, failing closed; reads only, no model, no network.

        A 022 derived portrayal layers its base chain's placements beneath its own (046), live: each
        ancestor's current art, slot by slot, unless the portrayal sets, hides or re-crops that slot.
        An instance layers its derivation entity's whole lineage beneath its campaign placements.
        """
        if isinstance(subject, SourceRef) and subject.pinned:
            raise ValueError(f"placements attach to the entity, not one revision: {subject.unpinned()}")
        with self.store.read_pass():  # the lock: one consistent state for every read below
            disk, state = _Disk(self.store), _State()
            problems: list[Problem] = []
            if isinstance(subject, SourceRef):
                found = disk.definition(subject)
                if not isinstance(found, StoredDefinition):
                    return Resolution(subject, (), (Problem(str(subject), str(found), "no slots are shown"),))
                layers = _lineage_layers(disk, disk.lineage(subject), problems)
                return resolve_lineage(subject, layers, _facts(state, disk, [layer.entries for layer in layers]),
                                       problems=problems)
            instance = disk.instance(subject)
            if instance == "missing":
                return Resolution(subject, (), (Problem(str(subject), "missing_subject", "no such instance"),))
            entity = None
            if isinstance(instance, StoredRecord):
                entity, code = derivation(instance.envelope)
                if code is not None:
                    problems.append(Problem(str(subject), code, "nothing is inherited"))
            else:
                problems.append(Problem(str(subject), str(instance), "derivation unknown; nothing is inherited"))
            layers = []
            if entity is not None:
                found = disk.definition(entity)
                if isinstance(found, StoredDefinition):
                    layers = _lineage_layers(disk, disk.lineage(entity), problems)
                else:
                    problems.append(Problem(str(entity), str(found), "nothing is inherited"))
            own, own_problems, trusted = disk.layer(subject)
            problems.extend(own_problems)
            campaign = _guarded(subject, own, trusted, layers)
            layers.append(Layer(subject, campaign, f"campaign:{subject.campaign_id}",
                                also=entity.source if entity is not None and len(layers) else None))
            facts = _facts(state, disk, [layer.entries for layer in layers])
            return resolve_lineage(subject, layers, facts, problems=problems)


def _guarded(subject: SourceRef | InstanceRef, own: Mapping[str, PlacementEntry], trusted: bool,
             beneath: list[Layer]) -> dict[str, PlacementEntry]:
    """A damaged layer (a linked, unreadable or look-alike folder or file) might be hiding a ``hide``:
    every slot it would inherit, and any ``adjust`` of one, shows ``invalid_placement`` instead of the
    picture beneath it. 018's rule for a campaign layer, and 046's for a derived portrayal's own."""
    entries = dict(own)
    if trusted:
        return entries
    for slot in sorted({name for layer in beneath for name in layer.entries}):
        entry = entries.get(slot)
        if entry is None or (entry.placement is not None and entry.placement.mode in ("adjust", "inherit")):
            entries[slot] = PlacementEntry(PlacementRef(subject, slot), None, "invalid_placement",
                                           "campaign layer damaged" if isinstance(subject, InstanceRef)
                                           else "portrayal layer damaged")
    return entries


def _lineage_layers(disk: _Disk, found: Lineage, problems: list[Problem]) -> list[Layer]:
    """One ``Layer`` per entity of a lineage, root first; a derived layer's damage is guarded."""
    problems.extend(found.problems)
    layers: list[Layer] = []
    for entity, derived in zip(found.entities, found.derived):
        own, own_problems, trusted = disk.layer(entity)
        problems.extend(own_problems)
        entries = _guarded(entity, own, trusted, layers) if derived else dict(own)
        layers.append(Layer(entity, entries, entity.source, derived))
    return layers


__all__ = ["ImageBatch", "ImageCommit", "ImageLibrary", "InvalidImage", "InvalidPlacement", "derivation"]
