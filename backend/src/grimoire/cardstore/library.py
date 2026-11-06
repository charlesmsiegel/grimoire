"""Approved, world-less card import over the native store (Sprint 037). An adapter.

``CardImporter.plan`` decodes card bytes, maps them to source records with ``grimoire.cards``,
chooses the destination source, stages every write in one 018 ``ImageBatch`` (records, manifest,
portrait image and its ``avatar`` placement) and asks the handle to ``propose`` each staged path
with its exact bytes. Nothing is written until ``CardImport.commit``, which goes through 010's
guarded writer, so an untrusted handle needs a covering grant *and* an approval bound to the
exact bytes of every path. See docs/CARDS.md.

The destination is chosen here, by trusted code: an explicit ``target``, the source an identical
earlier import went to, or a new ``card:<slug>``. A card's text never names a path, a source or an
identity, and nothing it says is ever written as an event, evidence or belief.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

from grimoire.cards import (
    CHARACTER, IMPORT_ID, IMPORT_KIND, IMPORT_SCHEMA, LOREBOOK, LOREBOOK_SCHEMA, SCENARIO, SCENARIO_SCHEMA,
    CardError, CharacterCard, LorebookCard, Loss, ParsedCard, Piece, ScenarioCard, Suggestions,
    character_payload, lorebook_payload, mention_texts, parse_card, parse_lorebook_document, pieces,
    key_path, scenario_payload, slugify, suggest_links,
)
from grimoire.cards.art import ArtPiece, asset_path, card_art
from grimoire.cards.records import ENTITY_SCHEMA
from grimoire.core.identity import ImageRef, SourceRef, validate_source_id
from grimoire.imagestore import ImageCommit, ImageLibrary
from grimoire.security import Permission
from grimoire.store import (
    InvalidEnvelope, LibraryStore, RecordEnvelope, RecordNotFound, SourceManifest, StoredDefinition, StoredSource,
    ImpreciseNumber, InvalidSourceState, NameCollision, StoreError,
    UnstorableIdentifier,
)
from grimoire.store.envelope import source_document
from grimoire.store.jsonfmt import JsonError, is_imprecise, parse_strict, render, thaw
from grimoire.store.layout import definition_parts, manifest_parts

from .art import PORTRAIT_SLOT, ArtSlot, recorded_art, stage_art

#: the largest card or separate lorebook file this slice reads
MAX_CARD_BYTES = 2 * 1024 * 1024
#: an origin label (a file name, say) is short display text
MAX_ORIGIN = 512
MAX_LOSSES_PER_FIELD = 20
MAX_LOSSES = 200
#: the namespace of a source this importer creates
CARD_NAMESPACE = "card"
_REVISION = re.compile(r"^r([1-9][0-9]{0,8})$")
_MAX_PROBE = 10_000
_PNG = b"\x89PNG\r\n\x1a\n"
_ZIP = (b"PK\x03\x04", b"PK\x05\x06")


class ImportDenied(PermissionError):
    """The handle may not stage this import; nothing was staged. ``denied`` lists ``(path, reason)``."""

    def __init__(self, message: str, denied: tuple[tuple[str, str], ...]) -> None:
        super().__init__(message)
        self.denied = denied

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.denied))


@dataclass(frozen=True)
class Collision:
    """Something already in the library that looks like this import. Reported, never merged.

    ``kind``: ``same_name`` (a definition elsewhere has the imported entity's name),
    ``same_bytes`` (another card source's latest import has these exact bytes),
    ``destination_taken`` (the default destination existed, so a suffixed one was chosen) or
    ``hand_edited`` (a head or the portrait slot was changed since the last import; it is left as it
    is unless the plan passes ``replace_edits=True``).
    """

    kind: str
    refs: tuple[str, ...]


@dataclass(frozen=True)
class CastStatus:
    """One cast member a scenario names, as written. ``status`` is ``resolves``, a 010 reason
    (``missing_source``, ``missing_entity`` ...), ``invalid_ref`` or ``name_only``; a name only
    produces candidates, never an identity."""

    written: str
    status: str
    candidates: tuple[SourceRef, ...] = ()


@dataclass(frozen=True)
class ImportReport:
    """What the import would do, for a human to approve. Card text is never echoed here except
    phrases that name existing definitions (``suggestions``) and cast entries as written."""

    status: str  # "new", "update" or "unchanged"
    source: str
    format: str
    kind: str  # "character", "scenario" or "lorebook": what the card became
    main: SourceRef  # the imported entity (unpinned)
    #: pinned revisions this import writes (new revisions only)
    writes: tuple[SourceRef, ...]
    #: pinned revisions the import record lists as this card's records after commit
    records: tuple[SourceRef, ...]
    #: entities of an earlier import that this card no longer has: their heads are left as they are
    kept: tuple[SourceRef, ...]
    pieces: tuple[Piece, ...]
    losses: tuple[Loss, ...]
    collisions: tuple[Collision, ...]
    suggestions: Suggestions
    cast: tuple[CastStatus, ...]
    portrait: ImageRef | None
    #: the exact logical paths a commit writes, and the grant scopes they fall under
    paths: tuple[str, ...]
    grant_scopes: tuple[str, ...]
    #: what this import turns into an accepted event: nothing, ever
    accepted_events: tuple[()] = ()
    #: each slot the card's art fills on ``main``, and what this import does there (Sprint 038)
    art: tuple[ArtSlot, ...] = ()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode(data: object, label: str) -> Mapping[str, object]:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError(f"{label} must be bytes")
    data = bytes(data)
    if len(data) > MAX_CARD_BYTES:
        raise CardError(f"{label} is larger than {MAX_CARD_BYTES} bytes", "too_large")
    if data.startswith(_PNG):
        raise CardError(f"{label}: PNG cards are a later milestone", "unsupported_format", "png")
    if data.startswith(_ZIP):
        raise CardError(f"{label}: CHARX archives are a later milestone", "unsupported_format", "charx")
    try:
        document = parse_strict(data)
    except JsonError:
        if not data.lstrip(b" \t\r\n").startswith(b"{") and _is_strict_json(data):
            raise CardError(f"{label} must be a JSON object", "invalid_card", "") from None
        raise CardError(f"{label} is not strict UTF-8 JSON", "invalid_json") from None
    if not isinstance(document, Mapping):
        raise CardError(f"{label} must be a JSON object", "invalid_card", "")
    return document


def _is_strict_json(data: bytes) -> bool:
    """Whether ``data`` is strict JSON that is not an object (wrapped so the store's parser reads it)."""
    try:
        wrapped = parse_strict(b'{"value":' + data + b"}")
    except JsonError:
        return False
    return len(wrapped) == 1  # ``1, "x": 2`` would wrap into a larger object


class _Imprecise:
    """A JSON number a double cannot hold exactly (found by re-reading the bytes)."""


def _imprecise_paths(data: bytes, dropped: Iterable[str] = (),
                     take: Callable[[str], str] | None = None) -> tuple[str, ...]:
    """Paths (``data.extensions.n``, ``tags[2]``) of numbers that become a nearby double, in document order.

    A number inside a value whose path is in ``dropped`` is skipped (it was not kept). A document
    with no such number is read once and nothing else is built. Otherwise paths are built only for
    the numbers found, each ancestor once (shared by its siblings), so the walk stays linear in the
    document. ``take`` decides each path: ``keep`` it, ``skip`` it, or ``stop`` the walk.
    """
    marker = _Imprecise()
    count = 0

    def on_float(text: str) -> object:
        nonlocal count
        if is_imprecise(text):
            count += 1
            return marker
        return 0.0

    try:
        document = json.loads(data.decode("utf-8"), parse_float=on_float, parse_int=lambda text: 0)
    except (ValueError, RecursionError):
        return ()
    if not count:
        return ()
    gone = set(dropped)  # read only now: most documents have no imprecise number
    # a node is (parent node, segment); its path and whether it lies in a dropped value, once each
    resolved: dict[int, tuple[object, str, bool]] = {}

    def resolve(node: tuple | None) -> tuple[str, bool]:
        pending: list[tuple] = []
        while node is not None and id(node) not in resolved:
            pending.append(node)
            node = node[0]
        path, inside = (resolved[id(node)][1:] if node is not None else ("", False))  # type: ignore[misc]
        for here in reversed(pending):
            segment = here[1]
            path = path + segment if segment.startswith("[") else (f"{path}.{segment}" if path else segment)
            inside = inside or path in gone
            resolved[id(here)] = (here, path, inside)
        return path, inside

    found: list[str] = []
    stack: list[tuple[object, tuple | None]] = [(document, None)]
    while stack:
        item, node = stack.pop()
        if item is marker:
            if node is None:
                continue  # a bare number is no document this reads
            parent, segment = node
            path, inside = resolve(parent)  # the number's own node is not kept: only its ancestors are
            path = path + segment if segment.startswith("[") else (f"{path}.{segment}" if path else segment)
            if not (inside or path in gone):
                verdict = take(path) if take is not None else "keep"
                if verdict == "keep":
                    found.append(path)
                elif verdict == "stop":
                    break
            continue
        if isinstance(item, dict):
            children = [(child, key_path("", key, index)) for index, (key, child) in enumerate(item.items())]
        elif isinstance(item, list):
            children = [(child, f"[{index}]") for index, child in enumerate(item)]
        else:
            continue
        stack.extend((child, (node, segment)) for child, segment in reversed(children))
    return tuple(found)


def _rounded(losses: Sequence[Loss], data: bytes, prefix: str) -> list[Loss]:
    """``rounded_number`` losses for imprecise numbers that were kept (not inside a dropped value).

    Only what ``_bounded`` can show is kept: a field's numbers up to its cap and one past it (its
    ``more_losses``), counting the losses already in the report first; the scan stops once the
    report could hold no more. What ``_bounded`` then keeps is what a full scan gives.
    """
    counts: dict[str, int] | None = None
    shown = 0

    def take(path: str) -> str:
        nonlocal counts, shown
        if counts is None:  # the losses already in the report count first (read only when needed)
            counts = {}
            for loss in losses:
                field = _field(prefix + loss.path)
                counts[field] = counts.get(field, 0) + 1
            shown = sum(min(count, MAX_LOSSES_PER_FIELD + 1) for count in counts.values())
        if shown > MAX_LOSSES:
            return "stop"
        field = _field(prefix + path)
        counts[field] = counts.get(field, 0) + 1
        if counts[field] > MAX_LOSSES_PER_FIELD + 1 and shown < MAX_LOSSES:
            return "skip"  # past the field's ``more_losses``: it cannot change the report
        shown += 1  # with the report full, any further loss is what makes ``_bounded`` add ``*``
        return "keep"

    paths = _imprecise_paths(data, (loss.path for loss in losses), take)
    return [Loss(prefix + path, "rounded_number") for path in paths]


_INDEX = re.compile(r"\[\d+\]|\{\d+\}")


def _field(path: str) -> str:
    """The field a loss counts toward: its path with each index or key position as ``[*]``/``{*}``."""
    return _INDEX.sub(lambda match: "[*]" if match.group().startswith("[") else "{*}", path) or "*"


def _bounded(losses: list[Loss]) -> list[Loss]:
    """At most ``MAX_LOSSES_PER_FIELD`` losses for one field (``tags[0]``…``tags[n]`` are one,
    ``tags[*]``, as are a mapping's unusual keys ``{0}``…``{n}``, ``{*}``), then one ``more_losses``
    at that field for the rest; at most ``MAX_LOSSES`` in all, then one ``more_losses`` at ``*``."""
    counts: dict[str, int] = {}
    kept: list[Loss] = []
    for loss in losses:
        if len(kept) >= MAX_LOSSES:
            kept.append(Loss("*", "more_losses"))
            break
        field = _field(loss.path)
        counts[field] = counts.get(field, 0) + 1
        if counts[field] <= MAX_LOSSES_PER_FIELD:
            kept.append(loss)
        elif counts[field] == MAX_LOSSES_PER_FIELD + 1:
            kept.append(Loss(field, "more_losses"))
    return kept


def _origin(origin: object) -> str | None:
    if origin is None:
        return None
    if type(origin) is not str or not origin.strip() or len(origin) > MAX_ORIGIN or any(
            ord(char) < 0x20 or 0x7F <= ord(char) < 0xA0 or 0xD800 <= ord(char) < 0xE000 for char in origin):
        raise CardError(f"origin must be a short single line of text (at most {MAX_ORIGIN})", "invalid_options",
                        "origin")
    return origin


def _canonical(value: object) -> str:
    """A payload as canonical JSON text, so ``true``, ``1`` and ``1.0`` stay different values."""
    return json.dumps(thaw(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _revision_number(revision: str | None) -> int:
    match = _REVISION.match(revision or "")
    return int(match.group(1)) if match else 0


@dataclass
class _Entity:
    key: str  # "<kind>/<id>"
    payload: dict[str, object]
    name: str
    schema: str

    @property
    def kind(self) -> str:
        return self.key.split("/", 1)[0]

    @property
    def entity_id(self) -> str:
        return self.key.split("/", 1)[1]


class CardImport:
    """One planned import: ``report``, the exact bytes (``preview``) and ``commit(approvals)``."""

    def __init__(self, report: ImportReport, batch: object | None, preview: Mapping[str, bytes]) -> None:
        self.report = report
        self._batch = batch
        self._preview = dict(preview)
        self._committed = False

    def preview(self) -> dict[str, bytes]:
        """The bytes each path will hold, keyed by logical path: what a human approves."""
        return dict(self._preview)

    def commit(self, approvals: Mapping[str, str] | None = None) -> ImageCommit:
        """Write exactly the previewed bytes, or nothing (``StaleWrite`` if the library moved,
        ``WriteDenied`` without a grant and an approval per path for an untrusted handle)."""
        if self._committed:
            raise StoreError("this import was already committed")
        if self._batch is None:  # unchanged: nothing to write
            self._committed = True
            return ImageCommit({}, (), ())
        result = self._batch.commit(approvals)  # type: ignore[attr-defined]
        self._committed = True
        return result


@dataclass
class _Plan:
    card: ParsedCard
    separate: LorebookCard | None
    original: bytes
    lorebook_bytes: bytes | None
    portrait: bytes | None
    origin: str | None
    as_scenario: bool
    replace_edits: bool = False
    losses: list[Loss] = field(default_factory=list)
    #: the slots the card and its portrait fill (Sprint 038)
    art: tuple[ArtPiece, ...] = ()


class CardImporter:
    """Plan card imports into ``store`` with ``handle`` (an untrusted worker handle, typically)."""

    def __init__(self, store: LibraryStore, handle: object, *, owner: str,
                 clock: Callable[[], str] = _now) -> None:
        if type(owner) is not str or not owner:
            raise ValueError("an importer needs the owner its new sources are recorded under")
        self.store = store
        self.handle = handle
        self.owner = owner
        self.clock = clock
        self.images = ImageLibrary(store, handle, owner=owner)

    # -- planning ----------------------------------------------------------------------------
    def plan(self, data: bytes, *, origin: str | None = None, target: str | None = None,
             portrait: bytes | None = None, lorebook: bytes | None = None,
             as_scenario: bool = False, replace_edits: bool = False) -> CardImport:
        """Stage one import; raises ``CardError`` (nothing staged) or ``ImportDenied``.

        A head or the portrait slot changed since the last import (a hand edit) is left as it is and
        reported as a ``hand_edited`` collision; ``replace_edits=True`` replaces it with the card's.
        """
        for label, flag in (("as_scenario", as_scenario), ("replace_edits", replace_edits)):
            if type(flag) is not bool:
                raise CardError(f"{label} is true or false", "invalid_options", label)
        document = _decode(data, "card")
        card = parse_card(document, as_scenario=as_scenario)
        separate = None
        lorebook_bytes = None
        if lorebook is not None:
            if isinstance(card, LorebookCard):
                raise CardError("a lorebook card takes no separate lorebook", "invalid_options", "lorebook")
            lorebook_bytes = bytes(lorebook) if isinstance(lorebook, (bytes, bytearray, memoryview)) else lorebook
            separate = parse_lorebook_document(_decode(lorebook_bytes, "lorebook"))
        if portrait is not None:
            if isinstance(card, LorebookCard):
                raise CardError("a lorebook has no portrait", "invalid_options", "portrait")
            if not isinstance(portrait, (bytes, bytearray, memoryview)):
                raise TypeError("portrait must be bytes")
            portrait = bytes(portrait)
        plan = _Plan(card, separate, bytes(data), lorebook_bytes, portrait, _origin(origin), as_scenario,
                     replace_edits)
        losses = list(card.losses) + _rounded(card.losses, plan.original, "")
        if separate is not None:
            losses += [Loss(f"lorebook_file.{loss.path}", loss.reason) for loss in separate.losses]
            losses += _rounded(separate.losses, lorebook_bytes, "lorebook_file.")
        plan.art, art_losses = card_art(card, portrait)
        plan.losses.extend(_bounded(losses + list(art_losses)))
        with self.store.read_pass():
            return self._stage(plan, target)

    def _digest(self, plan: _Plan) -> str:
        key = {"card": _sha(plan.original), "format": plan.card.format, "as_scenario": plan.as_scenario,
               "lorebook": _sha(plan.lorebook_bytes) if plan.lorebook_bytes is not None else None,
               "portrait": _sha(plan.portrait) if plan.portrait is not None else None}
        return _sha(json.dumps(key, sort_keys=True).encode("utf-8"))

    def _import_record(self, source_id: str) -> StoredDefinition | None:
        found = self.store.check_definition(SourceRef(source_id, IMPORT_KIND, IMPORT_ID))
        if not isinstance(found, StoredDefinition) or found.envelope.extra.get("schema") != IMPORT_SCHEMA:
            return None
        return found

    def _card_sources(self, sources: Mapping[str, StoredSource]) -> dict[str, StoredDefinition]:
        found = {}
        for source_id in sorted(sources):
            # only the card namespace: another source's import record never steers a destination
            if not source_id.startswith(f"{CARD_NAMESPACE}:"):
                continue
            if f"{IMPORT_KIND}/{IMPORT_ID}" not in sources[source_id].manifest.heads:
                continue
            record = self._import_record(source_id)
            if record is not None:
                found[source_id] = record
        return found

    def _destination(self, plan: _Plan, target: str | None, digest: str,
                     sources: Mapping[str, StoredSource], cards: Mapping[str, StoredDefinition]
                     ) -> tuple[str, list[Collision]]:
        collisions: list[Collision] = []
        same_bytes = sorted(source for source, record in cards.items()
                            if _get(record, "original", "sha256") == _sha(plan.original))
        if target is not None:
            try:
                validate_source_id(target)
                manifest_parts(target)
            except (ValueError, StoreError):
                raise CardError("target is not a source id this library can hold", "invalid_target",
                                "target") from None
            if target in sources and target not in cards:
                raise CardError("the target source was not made by a card import", "not_a_card_source", "target")
            if target not in sources and self._taken(target, sources):
                raise CardError("the target source exists but cannot be read as a card source", "not_a_card_source",
                                "target")
            if not target.startswith(f"{CARD_NAMESPACE}:"):
                raise CardError("a card source must be in the card namespace", "invalid_target", "target")
            chosen = target
        else:
            earlier = sorted(source for source, record in cards.items() if _get(record, "digest") == digest)
            if earlier:
                chosen = earlier[0]
            else:
                base = slugify(_title(plan.card))
                chosen = f"{CARD_NAMESPACE}:{base}"
                number = 1
                while self._taken(chosen, sources):
                    number += 1
                    chosen = f"{CARD_NAMESPACE}:{base}-{number}"
                if number > 1:
                    collisions.append(Collision("destination_taken", (f"{CARD_NAMESPACE}:{base}",)))
        others = tuple(source for source in same_bytes if source != chosen)
        if others:
            collisions.append(Collision("same_bytes", others))
        return chosen, collisions

    def _taken(self, source_id: str, sources: Mapping[str, StoredSource]) -> bool:
        """Whether ``source_id`` is in use: loaded, present but excluded or broken, or a spelling clash."""
        if source_id in sources:
            return True
        if self.store.check_source(source_id) != "missing_source":
            return True
        return self.store.collides("/".join(manifest_parts(source_id)))

    def _entities(self, plan: _Plan, previous: Mapping[str, str], heads: Mapping[str, str],
                  used_ids: frozenset[str] = frozenset()) -> tuple[dict[str, _Entity], list[Loss]]:
        """The entities this card becomes, keyed by role (``main``, ``lorebook``, ``lorebook_file``).

        A role keeps the entity an earlier import of this source gave it when its kind is the same
        (also one kept as ``<role>:<kind>`` after the role changed kind). A new entity takes an id
        that no existing head, earlier role, sibling or the import record uses, in any kind: the
        store refuses one entity id under two kinds in a source.
        """
        card, losses = plan.card, []
        taken: set[str] = set()

        def bare(key: str) -> str:
            return key.split("/", 1)[1]

        def key_for(role: str, kind: str, wanted: str) -> str:
            used = {bare(key) for key in taken}
            for earlier in (previous.get(role), previous.get(f"{role}:{kind}")):
                if earlier is not None and earlier.split("/", 1)[0] == kind and bare(earlier) not in used:
                    taken.add(earlier)
                    return earlier
            reserved = (used | used_ids | {bare(key) for key in heads} | {bare(key) for key in previous.values()}
                        | {IMPORT_ID})
            entity_id, number = wanted, 1
            while entity_id in reserved:
                number += 1
                entity_id = f"{wanted}-{number}"
            taken.add(f"{kind}/{entity_id}")
            return f"{kind}/{entity_id}"

        entities: dict[str, _Entity] = {}
        title = _title(card)
        if isinstance(card, LorebookCard):
            key = key_for("main", LOREBOOK, slugify(title, "lorebook"))
            entities["main"] = _Entity(key, lorebook_payload(card.lorebook, extras=card.extras), title,
                                       LOREBOOK_SCHEMA)
            return entities, losses
        kind = SCENARIO if isinstance(card, ScenarioCard) else CHARACTER
        main_key = key_for("main", kind, slugify(title))
        if isinstance(card, CharacterCard):
            payload, extra_losses = character_payload(card)
            losses.extend(extra_losses)
            entities["main"] = _Entity(main_key, payload, card.name, ENTITY_SCHEMA)
        else:
            entities["main"] = _Entity(main_key, scenario_payload(card), card.title, SCENARIO_SCHEMA)
        if card.lorebook is not None:
            lore_key = key_for("lorebook", LOREBOOK, f"{main_key.split('/', 1)[1]}-lore")
            entities["lorebook"] = _Entity(lore_key, lorebook_payload(card.lorebook),
                                           card.lorebook.name or f"{title} lore", LOREBOOK_SCHEMA)
        if plan.separate is not None:
            book = plan.separate.lorebook
            file_key = key_for("lorebook_file", LOREBOOK, slugify(book.name or f"{title} lore", "lorebook"))
            entities["lorebook_file"] = _Entity(file_key, lorebook_payload(book, extras=plan.separate.extras),
                                                book.name or f"{title} lore", LOREBOOK_SCHEMA)
        return entities, losses

    def _stage(self, plan: _Plan, target: str | None) -> CardImport:
        view = self.store.load()
        digest = self._digest(plan)
        cards = self._card_sources(view.sources)
        source_id, collisions = self._destination(plan, target, digest, view.sources, cards)
        stored_source = view.sources.get(source_id)
        record = cards.get(source_id)
        # what the last import made: role -> entity (kept roles carried forward) and the revision it wrote
        previous_entities = _roles(record, "entities")
        previous_kept = {role: key for role, key in _roles(record, "kept_entities").items()
                         if role not in previous_entities}
        imported = _revisions(record, "records") | _revisions(record, "kept")
        # ``declared``: the manifest's heads (what this import writes back); ``heads``: every entity's
        # current revision as load() resolves it, an undeclared only revision included. Ids are
        # reserved from every revision file, headed or not, as the commit's own check sees them.
        declared = dict(stored_source.manifest.heads) if stored_source is not None else {}
        heads = dict(declared)
        files = self.store.source_entities(source_id) if stored_source is not None else {}
        for kind, entity_id in files:
            if f"{kind}/{entity_id}" not in heads:
                found = self.store.check_definition(SourceRef(source_id, kind, entity_id))
                if isinstance(found, StoredDefinition) and found.ref.revision is not None:
                    heads[f"{kind}/{entity_id}"] = found.ref.revision
        entities, losses = self._entities(plan, previous_entities | previous_kept, heads,
                                          frozenset(entity_id for _, entity_id in files))
        plan.losses[:] = _bounded(plan.losses + losses)  # bounded once more: a bounded list re-bounds to itself

        writes: list[RecordEnvelope] = []
        records: dict[str, SourceRef] = {}
        edited: list[str] = []

        def settle(entity: _Entity) -> None:
            head = heads.get(entity.key)
            last = imported.get(entity.key)
            if head is not None and head != last and not plan.replace_edits:
                # changed by hand since the last import (or never imported here): never silently replaced
                edited.append(str(SourceRef(source_id, entity.kind, entity.entity_id, head)))
                records[entity.key] = SourceRef(source_id, entity.kind, entity.entity_id, last or head)
                return
            current = None
            if head is not None:
                found = self.store.check_definition(SourceRef(source_id, entity.kind, entity.entity_id, head))
                if isinstance(found, StoredDefinition):
                    current = found
            payload = _canonical(entity.payload)
            if (current is not None and _canonical(current.envelope.payload) == payload
                    and current.envelope.name == entity.name and current.envelope.extra.get("schema") == entity.schema):
                records[entity.key] = current.ref
                return
            revision = self._next_revision(source_id, entity, head)
            ref = SourceRef(source_id, entity.kind, entity.entity_id, revision)
            writes.append(RecordEnvelope(ref, entity.payload, name=entity.name, owner=self.owner,
                                         extra={"schema": entity.schema}))
            records[entity.key] = ref
            heads[entity.key] = declared[entity.key] = revision

        for role in ("lorebook", "lorebook_file"):
            if role in entities:
                settle(entities[role])
        main = entities["main"]
        if "lorebook" in entities and main.kind != LOREBOOK:
            pinned = str(records[entities["lorebook"].key])
            if main.kind == CHARACTER:
                main.payload["card"]["lorebook"] = pinned  # type: ignore[index]
            else:
                main.payload["lorebook"] = pinned
        if "lorebook_file" in entities and main.kind == SCENARIO and "lorebook" not in entities:
            main.payload["lorebook"] = str(records[entities["lorebook_file"].key])
        settle(main)
        main_ref = SourceRef(source_id, main.kind, main.entity_id)

        # the card's art (Sprint 038; the portrait as Sprint 037 placed it): ``avatars`` and ``art``
        # say what the last import placed in each slot, so a person's choice is never overwritten
        avatars = _mapping(_get(record, "avatars"))
        art_record = _mapping(_get(record, "art"))
        art = stage_art(self.images, source_id, main_ref, plan.art, recorded_art(avatars, art_record, main_ref),
                        plan.replace_edits, _provenance(plan), lambda index: asset_path(plan.card, index))
        plan.losses[:] = _bounded(plan.losses + art.losses)
        edited.extend(art.edited)
        avatars_after, art_after = dict(avatars), dict(art_record)
        if PORTRAIT_SLOT in art.recorded:
            avatars_after[str(main_ref)] = art.recorded[PORTRAIT_SLOT]
        others = {slot: text for slot, text in sorted(art.recorded.items()) if slot != PORTRAIT_SLOT}
        if others:
            art_after[str(main_ref)] = others
        portrait_shown = art.portrait is not None and art.recorded.get(PORTRAIT_SLOT) == str(art.portrait) \
            and any(entry.slot == PORTRAIT_SLOT and entry.status != "hand_edited" for entry in art.slots)

        import_key = f"{IMPORT_KIND}/{IMPORT_ID}"
        kept_roles: dict[str, str] = {}
        for role, key in sorted((previous_entities | previous_kept).items()):
            if key not in heads or key in records or key == import_key or key in kept_roles.values():
                continue
            base = role.split(":", 1)[0]
            name = base if base not in entities else f"{base}:{key.split('/', 1)[0]}"
            if name not in kept_roles:  # a role that changed kind keeps its old entity as ``<role>:<kind>``
                kept_roles[name] = key
        kept = tuple(SourceRef(source_id, *key.split("/", 1), heads[key]) for key in sorted(kept_roles.values()))
        kept_imported = sorted(str(SourceRef(source_id, *key.split("/", 1), imported[key]))
                               for key in kept_roles.values() if key in imported)
        record_refs = sorted(records.values(), key=str)
        unchanged = (record is not None and not writes and not art.writes
                     and _get(record, "digest") == digest
                     and _get(record, "records") == [str(ref) for ref in record_refs]
                     and _roles(record, "kept_entities") == kept_roles
                     and avatars == avatars_after and art_record == art_after)
        status = "unchanged" if unchanged else ("update" if stored_source is not None else "new")
        if not unchanged:
            import_payload = self._import_payload(plan, digest, entities, record_refs,
                                                  art.portrait if portrait_shown else None)
            if avatars_after:
                import_payload["avatars"] = dict(sorted(avatars_after.items()))
            if art_after:
                import_payload["art"] = dict(sorted(art_after.items()))
            if kept_roles:
                import_payload["kept_entities"] = dict(sorted(kept_roles.items()))
                import_payload["kept"] = kept_imported
            revision = self._next_revision(source_id, _Entity(import_key, {}, "", IMPORT_SCHEMA), heads.get(import_key))
            writes.append(RecordEnvelope(SourceRef(source_id, IMPORT_KIND, IMPORT_ID, revision), import_payload,
                                         name=f"Card import: {_title(plan.card)}"[:256], owner=self.owner,
                                         extra={"schema": IMPORT_SCHEMA}))
            heads[import_key] = declared[import_key] = revision

        batch, preview = None, {}
        if not unchanged:
            batch = self.images.change()
            try:
                batch.put(_manifest(source_id, stored_source, self.owner, plan, declared),
                          stored_source.etag if stored_source is not None else None)
                for envelope in writes:
                    batch.put(envelope, None)
                art.stage(batch, source_id, main_ref)
                preview = batch.preview()
                # the commit's own checks on every write (records, image, blob, placement), run
                # read-only now, so a plan that passes approval is not refused at commit for the
                # state of its source
                batch.check()
            except (InvalidSourceState, NameCollision, UnstorableIdentifier, ImpreciseNumber):
                raise CardError("the destination source cannot hold this import as it stands", "invalid_target",
                                "target") from None
            except InvalidEnvelope as error:
                if "larger than" in str(error):
                    raise CardError("an imported record would be larger than the store accepts", "too_large") from None
                raise CardError("the card cannot be written as records", "invalid_card", "") from None

        denied = []
        # an unchanged plan stages nothing, yet its report still reads the library: the handle must
        # still be allowed to propose in the destination
        asks = preview or {"/".join(manifest_parts(source_id)): render(
            source_document(stored_source.manifest, stored_source.etag))}  # type: ignore[union-attr]
        for path in sorted(asks):
            decision = self.handle.authorize(Permission.PROPOSE, path, asks[path])  # type: ignore[attr-defined]
            if not decision.allowed:
                denied.append((path, str(getattr(decision.reason, "value", decision.reason))))
        if denied:
            raise ImportDenied(f"{len(denied)} staged paths may not be proposed by this handle", tuple(denied))

        report = ImportReport(
            status=status, source=source_id, format=plan.card.format, kind=main.kind, main=main_ref,
            writes=tuple(envelope.address for envelope in writes if isinstance(envelope.address, SourceRef)),
            records=tuple(record_refs), kept=kept, pieces=pieces(plan.card, plan.separate.lorebook if plan.separate
                                                                  else None),
            losses=tuple(plan.losses),
            collisions=tuple(collisions + self._same_names(view, entities, source_id)
                             + ([Collision("hand_edited", tuple(edited))] if edited else [])),
            suggestions=suggest_links(
                mention_texts(plan.card, plan.separate.lorebook if plan.separate else None),
                view.index.find_by_name, exclude=lambda ref: ref.source == source_id),
            cast=self._cast(plan.card, view, source_id), portrait=art.portrait, paths=tuple(sorted(preview)),
            grant_scopes=tuple(sorted({_scope(path) for path in preview})), art=tuple(art.slots))
        return CardImport(report, batch, preview)

    def _import_payload(self, plan: _Plan, digest: str, entities: Mapping[str, _Entity],
                        records: list[SourceRef], portrait: ImageRef | None) -> dict[str, object]:
        payload: dict[str, object] = {
            "format": plan.card.format, "digest": digest, "imported_at": self.clock(), "imported_by": self.owner,
            "options": {"as_scenario": plan.as_scenario},
            "original": {"sha256": _sha(plan.original), "size": len(plan.original),
                         "base64": base64.b64encode(plan.original).decode("ascii")},
            "entities": {role: entity.key for role, entity in sorted(entities.items())},
            "records": [str(ref) for ref in records],
            "losses": [{"path": loss.path, "reason": loss.reason} for loss in plan.losses],
        }
        if plan.origin is not None:
            payload["origin"] = plan.origin
        if plan.lorebook_bytes is not None:
            payload["lorebook_file"] = {"sha256": _sha(plan.lorebook_bytes), "size": len(plan.lorebook_bytes),
                                        "base64": base64.b64encode(plan.lorebook_bytes).decode("ascii")}
        if plan.portrait is not None:
            # ``image`` is this import's picture when the slot shows it (``None`` while a hand edit stands)
            payload["portrait"] = {"image": str(portrait) if portrait is not None else None,
                                   "sha256": _sha(plan.portrait), "size": len(plan.portrait), "slot": PORTRAIT_SLOT}
        return payload

    def _next_revision(self, source_id: str, entity: _Entity, head: str | None) -> str:
        number = max(_revision_number(head), 0) + 1
        for _ in range(_MAX_PROBE):
            ref = SourceRef(source_id, entity.kind, entity.entity_id, f"r{number}")
            try:
                self.store.read(ref)
            except RecordNotFound:
                if not self.store.collides("/".join(definition_parts(ref))):
                    return ref.revision  # type: ignore[return-value]
            except UnstorableIdentifier:
                raise
            except (StoreError, ValueError):
                pass  # a revision file this version cannot read is still taken
            number += 1
        raise CardError("no free revision for an imported entity", "invalid_target", entity.key)

    def _same_names(self, view, entities: Mapping[str, _Entity], source_id: str) -> list[Collision]:  # type: ignore[no-untyped-def]
        found = []
        for role in ("main", "lorebook", "lorebook_file"):
            entity = entities.get(role)
            if entity is None:
                continue
            refs = tuple(str(ref) for ref in view.index.find_by_name(entity.name, entity.kind)
                         if ref.source != source_id)
            if refs:
                found.append(Collision("same_name", refs))
        return found

    def _cast(self, card: ParsedCard, view, source_id: str) -> tuple[CastStatus, ...]:  # type: ignore[no-untyped-def]
        if not isinstance(card, ScenarioCard):
            return ()
        found = []
        for cast in card.cast:
            if cast.ref is not None:
                try:
                    ref = SourceRef.parse(cast.ref)
                except ValueError:
                    found.append(CastStatus(cast.ref, "invalid_ref"))
                    continue
                try:
                    checked = self.store.check_definition(ref)
                except (UnstorableIdentifier, ValueError):  # a ref no portable path can hold
                    found.append(CastStatus(cast.ref, "invalid_ref"))
                    continue
                found.append(CastStatus(cast.ref, "resolves" if isinstance(checked, StoredDefinition)
                                        else str(checked)))
            else:
                name = cast.name or ""
                candidates = tuple(ref for ref in view.index.find_by_name(name) if ref.source != source_id)
                found.append(CastStatus(name, "name_only", candidates))
        return tuple(found)


def _title(card: ParsedCard) -> str:
    if isinstance(card, CharacterCard):
        return card.name
    if isinstance(card, ScenarioCard):
        return card.title
    return card.lorebook.name or "Lorebook"


def _manifest(source_id: str, stored: StoredSource | None, owner: str, plan: _Plan,
              heads: Mapping[str, str]) -> SourceManifest:
    """The manifest an import writes: an existing source keeps its owner, title, links and unknown fields."""
    if stored is not None:
        old = stored.manifest
        return SourceManifest(source_id, old.owner, old.title, heads, old.links, old.extra)
    return SourceManifest(source_id, owner, _title(plan.card)[:256], heads)


def _get(record: StoredDefinition | None, *path: str) -> object:
    value: object = thaw(record.envelope.payload) if record is not None else None
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _roles(record: StoredDefinition | None, key: str) -> dict[str, str]:
    found = _get(record, key)
    if not isinstance(found, dict):
        return {}
    return {role: value for role, value in found.items() if isinstance(role, str) and isinstance(value, str)
            and value.count("/") == 1}


def _revisions(record: StoredDefinition | None, key: str) -> dict[str, str]:
    """``kind/id`` -> the revision the record lists for it."""
    found: dict[str, str] = {}
    listed = _get(record, key)
    for text in listed if isinstance(listed, list) else ():
        try:
            ref = SourceRef.parse(text) if isinstance(text, str) else None
        except ValueError:
            ref = None
        if ref is not None and ref.pinned:
            found[f"{ref.kind}/{ref.entity_id}"] = ref.revision  # type: ignore[assignment]
    return found


def _mapping(value: object) -> dict[str, object]:
    return {key: item for key, item in value.items() if isinstance(key, str)} if isinstance(value, dict) else {}


def _image(text: object) -> ImageRef | None:
    if not isinstance(text, str) or "#image/" not in text:
        return None
    scope, image_id = text.split("#image/", 1)
    try:
        return ImageRef(scope, image_id)
    except ValueError:
        return None


def _scope(path: str) -> str:
    parts = path.split("/")
    if parts[:2] == ["assets", "blobs"]:
        return "assets/blobs"
    index = parts.index("_source") if "_source" in parts else len(parts) - 1
    return "/".join(parts[:index + 1])


def _provenance(plan: _Plan) -> dict[str, object]:
    found: dict[str, object] = {"origin": "character card import", "x-card-sha256": _sha(plan.original),
                                "x-card-format": plan.card.format}
    if plan.origin is not None:
        found["x-card-origin"] = plan.origin
    return found
