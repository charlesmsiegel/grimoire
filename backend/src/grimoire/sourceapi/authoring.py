"""Authoring source definitions over the native store (109): the library's catalog, one record's detail, and
new revisions of characters, scenarios, lorebooks, worlds and derived portrayals. An adapter.

``SourceAuthoring`` is trusted application code acting for the library's owner, as 044's ``TextApi`` is. It
writes nothing a domain service does not already define:

- a **definition revision** is immutable (010), so every edit is a *new* revision ``r<n+1>`` and a head move
  in the source manifest, in one guarded transaction. The manifest is written at the etag the editor read, so
  an edit made from a stale read is refused (``StaleWrite``) and writes nothing;
- a **derived portrayal** is 022's delta: a payload ``derivation`` with a pinned base, plus a
  ``derived_variant`` link that the portrayal's own source declares for that exact revision. One-field
  overrides, typed clears and resets are 046's pure helpers (``grimoire.core.inheritance``);
- unknown fields are kept: a structured edit starts from the head's payload and changes only the keys it
  names, and the advanced raw editor writes the payload exactly as given (an entity payload must still decode).

Nothing is matched by name, no chat or campaign record is written (attaching a revision to a chat is 107's
``ChatLibrary.attach_source``) and no model is called. Deleting a source or a revision is not offered: the
store has no delete for them, so chats that pin a revision keep it. See docs/SOURCE_AUTHORING.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from grimoire.cards import IMPORT_KIND, LOREBOOK_SCHEMA, SCENARIO_SCHEMA, scenario_lorebook
from grimoire.cards.lore import read_lorebook
from grimoire.cards.records import section_ok, slugify
from grimoire.generation.imported import CARD_FORMAT
from grimoire.context import MAX_KEYS, SkippedEntry, valid_key
from grimoire.core.entity_codec import ENTITY_SCHEMA, CodecError, _facet_payload, definition_from_payload
from grimoire.core.identity import SEGMENT_MAX, SourceRef, validate_segment, validate_source_id
from grimoire.core.index import DefinitionLink, MissingDefinition
from grimoire.core.inheritance import (
    OverrideError,
    clear_description,
    clear_facet,
    clear_section,
    override_description,
    override_section,
    reset_description,
    reset_facet,
    reset_section,
)
from grimoire.core.portrayal import MAX_CHAIN, PortrayalError, compose
from grimoire.effective.inspect import compose_in, inspect_definition, view_reader
from grimoire.effective.library import explainer
from grimoire.store import (
    CommitResult,
    LibraryStore,
    LibraryView,
    LinkSpec,
    RecordEnvelope,
    SourceManifest,
    StaleWrite,
    StoredDefinition,
)
from grimoire.store.jsonfmt import thaw

#: the entity families an editor knows by their record schema
ENTITY, SCENARIO, LOREBOOK, OTHER = "entity", "scenario", "lorebook", "other"
SCHEMA_OF = {ENTITY: ENTITY_SCHEMA, SCENARIO: SCENARIO_SCHEMA, LOREBOOK: LOREBOOK_SCHEMA}
#: kinds whose records are scenarios or lorebooks; any other kind is a 009 entity
FAMILY_OF_KIND = {"scenario": SCENARIO, "lorebook": LOREBOOK}
#: the namespace the card importer (037) owns: a person edits records there, but never makes such a source
CARD_NAMESPACE = "card"
NATURES = ("individual", "template")
#: 046's one-field portrayal edits
PORTRAYAL_EDITS: dict[str, Callable[..., dict]] = {
    "override_section": override_section, "clear_section": clear_section, "reset_section": reset_section,
    "override_description": override_description, "clear_description": clear_description,
    "reset_description": reset_description, "clear_facet": clear_facet, "reset_facet": reset_facet,
}
_CLEARS = {"clear_section": "section:{}", "clear_description": "description", "clear_facet": "facet:{}"}
_REVISION = re.compile(r"^r([1-9][0-9]{0,8})$")
MAX_TITLE = 256
MAX_TEXT = 100_000
MAX_ENTRIES = 4096
MAX_OPENINGS = 64
MAX_QUERY = 200


class AuthoringRefused(ValueError):
    """An authoring request refused before anything was written; ``reason`` is machine-readable."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, str(self)))


def family_of_schema(schema: object) -> str:
    if schema == ENTITY_SCHEMA:
        return ENTITY
    if schema == SCENARIO_SCHEMA:
        return SCENARIO
    if schema == LOREBOOK_SCHEMA:
        return LOREBOOK
    return OTHER


def family_of_kind(kind: str) -> str:
    return FAMILY_OF_KIND.get(kind, ENTITY)


def _schema(stored: StoredDefinition) -> object:
    return stored.envelope.extra.get("schema")


def _plain(value: object) -> Any:
    return thaw(value)


def _text(value: object, label: str, *, required: bool = False, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        if required:
            raise AuthoringRefused("invalid_field", f"{label} is required")
        return None
    if type(value) is not str:
        raise AuthoringRefused("invalid_field", f"{label} must be text")
    if required and not value.strip():
        raise AuthoringRefused("invalid_field", f"{label} must not be blank")
    if len(value) > limit:
        raise AuthoringRefused("invalid_field", f"{label} is longer than {limit} characters")
    return value


def _revision_number(revision: str) -> int:
    match = _REVISION.match(revision)
    return int(match.group(1)) if match else 0


def next_revision(revisions: Iterable[str]) -> str:
    """The revision after every ``r<n>`` an entity has, headed or not: ids are never reused."""
    return f"r{max((_revision_number(revision) for revision in revisions), default=0) + 1}"


@dataclass(frozen=True)
class Written:
    """What one authoring write committed: the new pinned revision (``None`` for a new source) and the
    source manifest's new etag, which the next edit of this source must name."""

    ref: SourceRef | None
    source: str
    manifest_etag: str
    result: CommitResult


class SourceAuthoring:
    """The owner's source catalog and editors over one open library. ``handle`` is the owner's trusted
    store handle; every write goes through the store's guarded commit with it."""

    def __init__(self, store: LibraryStore, handle: object, *, owner: str) -> None:
        self.store = store
        self.handle = handle
        self.owner = owner

    # -- reading -----------------------------------------------------------------------------------------
    def load(self) -> LibraryView:
        return self.store.load()

    def head(self, view: LibraryView, ref: SourceRef) -> StoredDefinition:
        """The stored revision ``ref`` resolves to in ``view`` (its head when unpinned), or a refusal that
        names the store's reason (``missing_entity``, ``excluded_source``, ``entity_dropped``...)."""
        try:
            return view.index.resolve(ref)  # type: ignore[return-value]
        except MissingDefinition as error:
            reason = error.reason
            if ref.source in view.excluded:
                reason = "excluded_source"
            elif ref.unpinned() in {dropped.unpinned() for dropped in view.dropped}:
                reason = "entity_dropped"
            raise AuthoringRefused(reason, f"{ref} does not resolve ({reason})") from None

    def revisions(self, ref: SourceRef) -> tuple[str, ...]:
        return self.store.source_entities(ref.source).get((ref.kind, ref.entity_id), ())

    def inspect(self, view: LibraryView, ref: SourceRef):  # type: ignore[no-untyped-def]
        """046's inspection and the composed definition of an entity revision: ``(inspection, composed)``.
        A revision that does not compose raises 022's ``PortrayalError`` or ``MissingDefinition``."""
        return inspect_definition(view, ref), compose_in(view, ref)

    # -- writing: sources ----------------------------------------------------------------------------------
    def create_source(self, source_id: str, title: str | None = None) -> Written:
        """A new, empty source (a world, a catalog of reusable characters...). Never in the ``card``
        namespace, which the card importer owns, and never over a source that exists or is excluded."""
        try:
            validate_source_id(source_id)
        except (ValueError, TypeError) as error:
            raise AuthoringRefused("invalid_field", f"source: {error}") from None
        if source_id.split(":", 1)[0] == CARD_NAMESPACE:
            raise AuthoringRefused("reserved_namespace", "card sources are made by importing a card")
        title = _text(title, "title", limit=MAX_TITLE)
        if self.store.check_source(source_id) != "missing_source":
            raise AuthoringRefused("source_exists", f"{source_id} already exists")
        manifest = SourceManifest(source_id, self.owner, title or None, {}, ())
        transaction = self.store.transaction()
        transaction.put(manifest, None)
        key = transaction.staged()[-1]
        result = transaction.commit(self.handle)
        return Written(None, source_id, result.etags[key], result)

    def rename_source(self, source_id: str, title: str | None, *, expected: str) -> Written:
        """Change a source's title, nothing else (its heads, links and unknown keys are kept)."""
        stored = self._source(source_id)
        title = _text(title, "title", limit=MAX_TITLE)
        manifest = SourceManifest(source_id, stored.manifest.owner, title or None, stored.manifest.heads,
                                  stored.manifest.links, stored.manifest.extra)
        transaction = self.store.transaction()
        transaction.put(manifest, expected)
        key = transaction.staged()[-1]
        result = transaction.commit(self.handle)
        return Written(None, source_id, result.etags[key], result)

    # -- writing: definitions -------------------------------------------------------------------------------
    def create(self, source_id: str, kind: str, fields: Mapping[str, object], *, expected: str,
               entity_id: str | None = None) -> Written:
        """A new entity, scenario or lorebook at ``r1`` of ``source_id``. Its id is ``entity_id`` or a slug of
        its name; an id the source already uses, under any kind, is refused (ids are never reused)."""
        stored = self._source(source_id)
        if stored.etag != expected:
            raise StaleWrite([(source_id, "changed")])
        try:
            validate_segment(kind, "kind")
        except (ValueError, TypeError) as error:
            raise AuthoringRefused("invalid_field", f"kind: {error}") from None
        if kind == IMPORT_KIND:
            raise AuthoringRefused("import_record_read_only", "an import record is written only by its importer")
        family = family_of_kind(kind)
        payload = self._apply_fields(family, {}, fields, derived=False)
        entity_id = self._new_id(source_id, entity_id, self._display_name(family, payload), kind)
        ref = SourceRef(source_id, kind, entity_id, "r1")
        return self._write(stored, ref, family, payload, SCHEMA_OF[family])

    def save(self, ref: SourceRef, fields: Mapping[str, object], *, expected: str, head: str, etag: str) -> Written:
        """A new revision of ``ref``'s entity with the structured ``fields`` changed and everything else kept."""
        stored, current = self._editable(ref, expected, head, etag)
        family = family_of_schema(_schema(current))
        payload = _plain(current.envelope.payload)
        if not isinstance(payload, dict):
            raise AuthoringRefused("invalid_payload", f"{current.ref}'s payload is not an object; use the raw editor")
        payload = self._apply_fields(family, payload, fields, derived="derivation" in payload)
        return self._write(stored, self._next(current.ref), family, payload, _schema(current),
                           current.envelope.extra)

    def save_raw(self, ref: SourceRef, payload: object, *, expected: str, head: str, etag: str) -> Written:
        """A new revision holding ``payload`` exactly as given (unknown fields included); its schema, or the
        absence of one, and its other envelope fields are the head's own. An entity payload must decode (009);
        a scenario's lorebook must be a pinned ref."""
        stored, current = self._editable(ref, expected, head, etag)
        family = family_of_schema(_schema(current))
        if not isinstance(payload, dict):
            raise AuthoringRefused("invalid_payload", "a record's payload must be a JSON object")
        return self._write(stored, self._next(current.ref), family, payload, _schema(current),
                           current.envelope.extra)

    def derive(self, base: SourceRef, source_id: str, *, expected: str, entity_id: str | None = None,
               name: str | None = None) -> Written:
        """A new derived portrayal of ``base`` (pinned; an unpinned ref is pinned to its head now) in
        ``source_id``: it inherits every field until one is overridden or cleared. Its source declares the
        ``derived_variant`` link; the base and every other portrayal are untouched."""
        view = self.load()
        stored = self._source(source_id)
        base_stored = self.head(view, base)
        base = base_stored.ref
        if family_of_schema(_schema(base_stored)) != ENTITY:
            raise AuthoringRefused("not_entity_definition", f"{base} is not an entity; only entities have portrayals")
        try:
            composed = compose_in(view, base)
        except (PortrayalError, MissingDefinition) as error:
            raise AuthoringRefused(getattr(error, "reason", "missing_base"),
                                   f"{base} does not compose, so nothing can derive from it ({error})") from None
        if len(composed.chain) >= MAX_CHAIN:
            raise AuthoringRefused("chain_too_deep", f"{base} already composes {len(composed.chain)} layers")
        name = _text(name, "name") if name is not None else None
        if name is not None and not name.strip():
            raise AuthoringRefused("invalid_field", "name must not be blank")
        entity_id = self._new_id(source_id, entity_id, name or composed.name, base.kind)
        payload = {"name": name or composed.name, "nature": composed.nature, "traits": [],
                   "derivation": {"base": str(base)}}
        if stored.etag != expected:
            raise StaleWrite([(source_id, "changed")])
        return self._write(stored, SourceRef(source_id, base.kind, entity_id, "r1"), ENTITY, payload,
                           ENTITY_SCHEMA)

    def edit_portrayal(self, ref: SourceRef, edit: str, *, expected: str, head: str, etag: str,
                       name: str | None = None, text: str | None = None) -> Written:
        """One 046 field edit of a derived portrayal as its next revision: override, typed clear or reset of a
        section, the description or a facet. A clear of something the portrayal does not inherit is refused
        here (``stale_clear``) rather than written and refused on composition."""
        if edit not in PORTRAYAL_EDITS:
            raise AuthoringRefused("invalid_field", f"edit must be one of {', '.join(sorted(PORTRAYAL_EDITS))}")
        stored, current = self._editable(ref, expected, head, etag)
        payload = _plain(current.envelope.payload)
        if family_of_schema(_schema(current)) != ENTITY or not isinstance(payload, dict):
            raise AuthoringRefused("not_derived", f"{current.ref} is not a derived portrayal")
        args: list[object] = [payload]
        if "section" in edit or "facet" in edit:
            args.append(_text(name, "name", required=True))
        if edit.startswith("override"):
            args.append(_text(text, "text", required=True))
        if edit in _CLEARS:
            field_name = _CLEARS[edit].format(*(args[1:2]))
            self._require_inherited(current.ref, field_name)
        try:
            edited = PORTRAYAL_EDITS[edit](*args)
        except OverrideError as error:
            raise AuthoringRefused(error.reason, str(error)) from None
        except (TypeError, ValueError, AttributeError) as error:  # a raw edit left a malformed derivation
            raise AuthoringRefused("invalid_payload", f"{current.ref}'s derivation is malformed; use the raw "
                                                      f"editor ({error})") from None
        if edit == "override_section" and not section_ok(args[1], args[2]):  # type: ignore[arg-type]
            raise AuthoringRefused("invalid_sections", "that section text is not one 022 composes")
        return self._write(stored, self._next(current.ref), ENTITY, edited, _schema(current),
                           current.envelope.extra)

    # -- internals -------------------------------------------------------------------------------------------
    def _source(self, source_id: str):  # type: ignore[no-untyped-def]
        try:
            validate_source_id(source_id)
        except (ValueError, TypeError) as error:
            raise AuthoringRefused("invalid_field", f"source: {error}") from None
        found = self.store.check_source(source_id)
        if isinstance(found, str):
            raise AuthoringRefused(found, f"{source_id} cannot be edited ({found})")
        return found

    def _editable(self, ref: SourceRef, expected: str, head: str, etag: str) -> tuple[Any, StoredDefinition]:
        """The source and the head revision an edit replaces, checked against what the editor read: another
        head, another manifest or another head record (``etag``: its canonical file edited by hand since) is
        ``StaleWrite`` and nothing is written (the client keeps its draft)."""
        stored = self._source(ref.source)
        if ref.kind == IMPORT_KIND:
            raise AuthoringRefused("import_record_read_only", "an import record is written only by its importer")
        current = self.head(self.load(), ref.unpinned())
        if ref.pinned and ref.revision != current.ref.revision:
            raise AuthoringRefused("not_head", f"{ref} is an older revision; only the head ({current.ref}) is edited")
        if current.ref.revision != head or stored.etag != expected or current.etag != etag:
            raise StaleWrite([(str(ref.unpinned()), "changed")])
        return stored, current

    def _new_id(self, source_id: str, chosen: str | None, name: str, kind: str) -> str:
        """``chosen``, refused if the source already uses it under any kind (ids are never reused); without
        one, a slug of ``name`` with the first free ``-2``, ``-3``... suffix."""
        taken = {entity_id for _kind, entity_id in self.store.source_entities(source_id)}
        if chosen is None:
            stem = slugify(name, kind)
            chosen, number = stem, 1
            while chosen in taken:
                number += 1
                chosen = f"{stem[:SEGMENT_MAX - len(str(number)) - 1].rstrip('-')}-{number}"
        try:
            validate_segment(chosen, "entity_id")
        except (ValueError, TypeError) as error:
            raise AuthoringRefused("invalid_field", f"entity_id: {error}") from None
        if chosen in taken:
            raise AuthoringRefused("entity_exists", f"{source_id} already has an entity {chosen}")
        return chosen

    def _next(self, ref: SourceRef) -> SourceRef:
        return ref.at(next_revision(self.revisions(ref)))

    def _require_inherited(self, ref: SourceRef, field_name: str) -> None:
        try:
            inspection, _composed = self.inspect(self.load(), ref)
        except (PortrayalError, MissingDefinition) as error:
            raise AuthoringRefused(getattr(error, "reason", "missing_base"),
                                   f"{ref} does not compose, so nothing can be cleared ({error})") from None
        found = inspection.field(field_name)
        if found is None or found.state not in ("inherited", "pinned", "overridden", "restated"):
            raise AuthoringRefused("stale_clear", f"{field_name} is not inherited by {ref}, so there is nothing "
                                                  "to clear")

    @staticmethod
    def _display_name(family: str, payload: Mapping[str, object]) -> str:
        key = "title" if family == SCENARIO else "name"
        value = payload.get(key)
        return value if isinstance(value, str) and value.strip() else ""

    def _apply_fields(self, family: str, payload: dict, fields: Mapping[str, object], *, derived: bool) -> dict:
        if not isinstance(fields, Mapping):
            raise AuthoringRefused("invalid_field", "fields must be an object")
        apply = {ENTITY: _entity_fields, SCENARIO: _scenario_fields, LOREBOOK: _lorebook_fields}.get(family)
        if apply is None:
            raise AuthoringRefused("no_structured_editor", "this record has no structured editor; use the raw "
                                                           "editor")
        return apply(payload, fields, derived)

    def _write(self, stored, ref: SourceRef, family: str, payload: dict,  # type: ignore[no-untyped-def]
               schema: object, carried: Mapping[str, object] | None = None) -> Written:
        """Write ``payload`` as ``ref`` and move its head. ``schema`` is the record's own (``None``: it has
        none, kept absent); ``carried`` is the replaced head's envelope fields, kept on the new revision."""
        name = _validate(ref, family, schema, payload)
        manifest = stored.manifest
        heads = dict(manifest.heads)
        heads[f"{ref.kind}/{ref.entity_id}"] = ref.revision
        links = list(manifest.links)
        derivation = payload.get("derivation") if family == ENTITY else None
        if isinstance(derivation, Mapping):
            link = LinkSpec(str(ref), "derived_variant", str(derivation["base"]))
            if not any((existing.subject, existing.relation, existing.object) ==
                       (link.subject, link.relation, link.object) for existing in links):
                links.append(link)
        if family == ENTITY:
            self._require_composes(ref, payload, link if isinstance(derivation, Mapping) else None)
        extra = {key: value for key, value in (carried or {}).items() if key != "schema"}
        if schema is not None:
            extra["schema"] = schema
        envelope = RecordEnvelope(ref, payload, name=name, owner=self.owner, extra=extra)
        transaction = self.store.transaction()
        transaction.put(envelope, None)
        transaction.put(SourceManifest(manifest.source, manifest.owner, manifest.title, heads, tuple(links),
                                       manifest.extra), stored.etag)
        key = transaction.staged()[-1]
        result = transaction.commit(self.handle)
        return Written(ref, ref.source, result.etags[key], result)

    def _require_composes(self, ref: SourceRef, payload: dict, link: LinkSpec | None) -> None:
        """Refuse an entity revision that would not compose (022): a derivation whose base does not exist or
        is not yet written, a clear of something it also sets, another nature than its base's... It is
        composed over the current library as if it were written, so a head that cannot be played is never
        written by an editor."""
        view = self.load()
        definition = definition_from_payload(ref, ENTITY_SCHEMA, payload)
        extra = (DefinitionLink(SourceRef.parse(link.subject), link.relation, SourceRef.parse(link.object),
                                ref.source),) if link is not None else ()
        reader = view_reader(view)

        def read(current: SourceRef):  # type: ignore[no-untyped-def]
            return (definition, None) if current == ref else reader(current)

        try:
            compose(ref, _Candidate(view.index, ref, definition, extra), read, explainer(view))
        except (PortrayalError, MissingDefinition) as error:
            reason = getattr(error, "reason", "missing_base")
            raise AuthoringRefused(reason, f"{ref} would not compose ({reason}): {error}") from None


class _Candidate:
    """The library's index as it would be with one more entity revision (and its own derivation link)."""

    def __init__(self, index, ref: SourceRef, definition: object, links: tuple) -> None:  # type: ignore[no-untyped-def]
        self.index, self.ref, self.definition, self.extra = index, ref, definition, links

    def resolve(self, ref: SourceRef):  # type: ignore[no-untyped-def]
        return self.definition if ref == self.ref else self.index.resolve(ref)

    def check(self, ref: SourceRef):  # type: ignore[no-untyped-def]
        return None if ref == self.ref else self.index.check(ref)

    def links_for(self, ref: SourceRef) -> tuple:  # type: ignore[type-arg]
        key = ref.entity_key
        own = tuple(link for link in self.extra if key in (link.subject.entity_key, link.object.entity_key))
        return self.index.links_for(ref) + own


# -- payload validation -----------------------------------------------------------------------------------

def _validate(ref: SourceRef, family: str, schema: object, payload: dict) -> str | None:
    """The record's display name, after the checks a write must pass: an entity decodes (009) and a derived
    one names a pinned base of another entity; a scenario's lorebook, if any, is a pinned lorebook ref."""
    if family == ENTITY:
        try:
            definition = definition_from_payload(ref, ENTITY_SCHEMA, payload)
        except (CodecError, ValueError) as error:
            raise AuthoringRefused(getattr(error, "reason", "invalid_payload"), f"not a valid entity: {error}") from None
        if "derivation" in payload:
            derivation = payload["derivation"]
            base = derivation.get("base") if isinstance(derivation, Mapping) else None
            try:
                base_ref = SourceRef.parse(base)
            except (ValueError, TypeError):
                raise AuthoringRefused("invalid_derivation", "derivation.base must be a pinned ref") from None
            if not base_ref.pinned or base_ref.unpinned() == ref.unpinned() or base_ref.kind != ref.kind:
                raise AuthoringRefused("invalid_derivation", "derivation.base must pin another entity of this kind")
        return definition.name
    if family == SCENARIO:
        try:
            scenario_lorebook(schema, payload)
        except ValueError as error:
            raise AuthoringRefused("invalid_field", str(error)) from None
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip():
            raise AuthoringRefused("invalid_field", "a scenario needs a title")
        return title
    name = payload.get("name")
    return name if isinstance(name, str) and name.strip() else None


def _keep_example_speaker(edited: dict, old: object) -> None:
    """On a rename of an imported card character, record the name its example messages label its own turns
    with (``card.example_speaker``, read by 037's voice reader), once, so its voice keeps its own lines."""
    card = edited.get("card")
    if (isinstance(old, str) and old.strip() and edited["name"] != old and isinstance(card, Mapping)
            and card.get("format") == CARD_FORMAT and "example_speaker" not in card):
        edited["card"] = {**card, "example_speaker": old}


def _entity_fields(payload: dict, fields: Mapping[str, object], derived: bool) -> dict:
    allowed = {"name", "nature", "description", "sections", "traits"}
    unknown = set(fields) - allowed
    if unknown:
        raise AuthoringRefused("invalid_field", f"unknown fields: {', '.join(sorted(unknown))}")
    new = not payload
    edited = dict(payload)
    if "name" in fields or "name" not in edited:
        edited["name"] = _text(fields.get("name"), "name", required=True, limit=MAX_TITLE)
        _keep_example_speaker(edited, payload.get("name"))
    if "nature" in fields or "nature" not in edited:
        nature = fields.get("nature", "individual")
        if nature not in NATURES:
            raise AuthoringRefused("invalid_field", f"nature must be {' or '.join(NATURES)}")
        edited["nature"] = nature
    if "description" in fields:
        description = _text(fields["description"], "description")
        if derived:  # an override; blank drops a local override but never lifts a clear (that is "reset")
            derivation = edited.get("derivation")
            cleared = isinstance(derivation, Mapping) and derivation.get("clear_description") is True
            if description and description.strip():
                edited = _helper(override_description, edited, description)
            elif not cleared:
                edited = _helper(reset_description, edited)
        elif description:
            edited["description"] = description
        else:
            edited.pop("description", None)
    if "sections" in fields:
        sections = fields["sections"]
        if not isinstance(sections, Mapping):
            raise AuthoringRefused("invalid_field", "sections must map names to text")
        kept: dict[str, str] = {}
        for name, text in sections.items():
            text = _text(text, f"section {name}")
            if not text or not text.strip():
                continue  # an empty section is left out: on a portrayal it inherits (clears are typed)
            if not section_ok(name, text):
                raise AuthoringRefused("invalid_sections", f"section {name!r} is not one 022 composes")
            kept[name] = text
        if derived:  # each given section overrides; a local section left out inherits again
            before = edited.get("sections") if isinstance(edited.get("sections"), Mapping) else {}
            for name in sorted(set(before) - set(kept)):
                edited = _helper(reset_section, edited, name)
            for name, text in kept.items():
                edited = _helper(override_section, edited, name, text)
        elif kept:
            edited["sections"] = kept
        else:
            edited.pop("sections", None)
    if "traits" in fields:
        traits = fields["traits"]
        if not isinstance(traits, list) or not all(isinstance(item, Mapping) for item in traits):
            raise AuthoringRefused("invalid_field", "traits must be a list of facet objects")
        edited["traits"] = [dict(item) for item in traits]
    if new:
        edited.setdefault("traits", [])
    return edited


def _helper(edit: Callable[..., dict], payload: dict, *args: object) -> dict:
    try:
        return edit(payload, *args)
    except OverrideError as error:
        raise AuthoringRefused(error.reason, str(error)) from None
    except (TypeError, ValueError, AttributeError) as error:
        raise AuthoringRefused("invalid_payload", f"the derivation is malformed; use the raw editor ({error})") \
            from None


def _openings(value: object, before: object) -> list[dict]:
    if not isinstance(value, list) or len(value) > MAX_OPENINGS:
        raise AuthoringRefused("invalid_field", f"openings must be a list of at most {MAX_OPENINGS}")
    old = {item["id"]: item for item in before if isinstance(item, Mapping) and type(item.get("id")) is str} \
        if isinstance(before, list) else {}
    found: list[dict] = []
    used: set[str] = set()
    for number, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            raise AuthoringRefused("invalid_field", "each opening must be an object")
        text = _text(item.get("text"), f"opening {number}", required=True)
        opening_id = item.get("id")
        if opening_id is None:
            opening_id = "first" if not used and "first" not in old else f"opening-{number}"
            while opening_id in used or opening_id in old:
                number += 1000
                opening_id = f"opening-{number}"
        if type(opening_id) is not str or not opening_id or opening_id in used:
            raise AuthoringRefused("invalid_field", "opening ids must be distinct text")
        used.add(opening_id)
        entry = dict(old.get(opening_id, {}))  # an edited opening keeps its other keys (group_only...)
        entry.update({"id": opening_id, "text": text})
        found.append(entry)
    return found


def _scenario_fields(payload: dict, fields: Mapping[str, object], _derived: bool) -> dict:
    allowed = {"title", "premise", "openings", "instructions"}
    unknown = set(fields) - allowed
    if unknown:
        raise AuthoringRefused("invalid_field", f"unknown fields: {', '.join(sorted(unknown))}")
    edited = dict(payload)
    had_roles = "roles" in edited
    if had_roles and not isinstance(edited["roles"], Mapping):
        if set(fields) & {"premise", "instructions", "openings"}:
            raise AuthoringRefused("invalid_payload", "this scenario's roles are not an object; use the raw editor")
        roles: dict = {}
    else:
        roles = dict(edited.get("roles") or {})
    before_roles = dict(roles)
    if "title" in fields or "title" not in edited:
        edited["title"] = _text(fields.get("title"), "title", required=True, limit=MAX_TITLE)
    for key, role in (("premise", "scenario_premise"), ("instructions", "private_instruction")):
        if key in fields:
            value = _text(fields[key], key)
            if value and value.strip():
                edited[key] = value
                roles[key] = role
            else:
                edited.pop(key, None)
                roles.pop(key, None)
    if "openings" in fields:
        openings = _openings(fields["openings"], edited.get("openings"))
        if openings:
            edited["openings"] = openings
            roles["openings"] = "scenario_opening"
        else:
            edited.pop("openings", None)
            roles.pop("openings", None)
    if not (isinstance(edited.get("premise"), str) or edited.get("openings")):
        raise AuthoringRefused("invalid_field", "a scenario needs a premise or an opening")
    if roles != before_roles or (not had_roles and roles):
        edited["roles"] = roles
    return edited


def _keys(value: object, label: str, kept: object = ()) -> list[str]:
    """Nonblank keys, each one the lore reader can match (``valid_key``), at most ``MAX_KEYS``; a key the
    head's entry already had is kept as it is, so an imported entry stays editable."""
    if not isinstance(value, list) or not all(type(key) is str for key in value):
        raise AuthoringRefused("invalid_field", f"{label} must be a list of text")
    kept = kept if isinstance(kept, (list, tuple)) else ()
    keys = [key for key in value if key.strip()]
    for key in keys:
        if key not in kept and not valid_key(key.strip()):
            raise AuthoringRefused("invalid_field", f"{label}: {key[:40]!r} is not a key lore can match (one line "
                                                    "of at most 100 characters with a word)")
    if len(keys) > max(MAX_KEYS, len(kept)):
        raise AuthoringRefused("invalid_field", f"{label}: at most {MAX_KEYS} keys")
    return keys


def _lore_skip(entry: object) -> str | None:
    """Why the lore reader (037) would skip ``entry`` and never put it in context, or ``None``."""
    found = read_lorebook({"entries": [entry]})
    return found[0].reason if found and isinstance(found[0], SkippedEntry) else None


_LORE_SKIPS = {
    "content": "its content must be at most 8000 characters of plain text",
    "keys": "it needs a key, or to be constant",
    "use_regex": "regex keys are not matched; edit it in the raw editor",
    "secondary_keys": "a secondary key cannot be used",
}


def _lorebook_fields(payload: dict, fields: Mapping[str, object], _derived: bool) -> dict:
    allowed = {"name", "description", "entries"}
    unknown = set(fields) - allowed
    if unknown:
        raise AuthoringRefused("invalid_field", f"unknown fields: {', '.join(sorted(unknown))}")
    edited = dict(payload)
    for key in ("name", "description"):
        if key in fields:
            value = _text(fields[key], key, limit=MAX_TITLE if key == "name" else MAX_TEXT)
            if value and value.strip():
                edited[key] = value
            else:
                edited.pop(key, None)
    if "entries" in fields:
        entries = fields["entries"]
        before = edited.get("entries") if isinstance(edited.get("entries"), list) else []
        if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
            raise AuthoringRefused("invalid_field", f"entries must be a list of at most {MAX_ENTRIES}")
        found: list[dict] = []
        for number, item in enumerate(entries, start=1):
            if not isinstance(item, Mapping):
                raise AuthoringRefused("invalid_field", "each entry must be an object")
            origin = item.get("index")
            if origin is not None:
                if type(origin) is not int or not 0 <= origin < len(before) or not isinstance(before[origin], Mapping):
                    raise AuthoringRefused("invalid_field", f"entry {number}'s index names no entry of the head")
                entry = dict(before[origin])  # its settings, extensions and extras stay as they were
            else:
                entry = {}
            entry["keys"] = _keys(item.get("keys", entry.get("keys", [])), f"entry {number} keys", entry.get("keys"))
            entry["content"] = _text(item.get("content"), f"entry {number} content", required=True)
            if "constant" in item:  # 037's lore reader reads ``settings.constant``; its other settings stay
                if type(item["constant"]) is not bool:
                    raise AuthoringRefused("invalid_field", f"entry {number} constant must be true or false")
                settings = entry.get("settings", {})
                if not isinstance(settings, Mapping):
                    raise AuthoringRefused("invalid_payload", f"entry {number}'s settings are not an object; use the "
                                                              "raw editor")
                settings = dict(settings)
                if item["constant"] or "constant" in settings:
                    settings["constant"] = item["constant"]
                if settings or "settings" in entry:
                    entry["settings"] = settings
            if "enabled" in item:
                if type(item["enabled"]) is not bool:
                    raise AuthoringRefused("invalid_field", f"entry {number} enabled must be true or false")
                if item["enabled"] and "enabled" not in entry:
                    pass  # absent means enabled; nothing to write
                else:
                    entry["enabled"] = item["enabled"]
            skipped = _lore_skip(entry)
            if skipped is not None and (origin is None or _lore_skip(before[origin]) != skipped):
                raise AuthoringRefused("unusable_lore", f"entry {number} would never reach play ({skipped}): "
                                                        + _LORE_SKIPS.get(skipped, "the lore reader skips it"))
            found.append(entry)
        edited["entries"] = found
    if not payload:
        edited.setdefault("entries", [])
    return edited


def facet_value(facet: object) -> dict:
    """A facet as its record payload writes it, for display."""
    return _facet_payload(facet)  # type: ignore[arg-type]


__all__ = [
    "ENTITY", "LOREBOOK", "NATURES", "OTHER", "PORTRAYAL_EDITS", "SCENARIO", "AuthoringRefused", "SourceAuthoring",
    "Written", "facet_value", "family_of_kind", "family_of_schema", "next_revision",
]
