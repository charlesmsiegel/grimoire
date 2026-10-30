"""Facet-neutral record envelopes and source manifests.

The store reads and writes these documents without interpreting a record's
payload: it never decides whether a record is a character, a PC or an item.
Unknown top-level fields are preserved losslessly in ``extra``. Reference
fields are kept as the exact text written, so an unresolvable or unparsable
reference stays visible instead of being rebound or dropped.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.core.identity import InstanceRef, SourceRef, parse_ref, validate_segment, validate_source_id
from grimoire.core.index import DIRECTED_RELATIONS, SYMMETRIC_RELATIONS

from .errors import InvalidEnvelope
from .jsonfmt import check_writable, freeze, is_etag, thaw

RECORD_FORMAT = "grimoire.record/1"
SOURCE_FORMAT = "grimoire.source/1"

_RECORD_FIELDS = frozenset({"format", "address", "payload", "name", "owner", "derived_from", "base",
                            "base_etag", "previous"})
_INSTANCE_ONLY = ("derived_from", "base", "base_etag")
_SOURCE_FIELDS = frozenset({"format", "source", "owner", "title", "heads", "links", "previous"})
_LINK_FIELDS = frozenset({"subject", "relation", "object"})
RELATIONS = SYMMETRIC_RELATIONS | DIRECTED_RELATIONS

Address = SourceRef | InstanceRef


class EnvelopeError(ValueError):
    """A stored document is not a readable envelope; ``code`` is the load problem code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _empty() -> Mapping[str, object]:
    return MappingProxyType({})


def _frozen_extra(extra: object, known: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(extra, Mapping) or not all(type(key) is str for key in extra):
        raise InvalidEnvelope("extra must be a mapping with text keys")
    clash = sorted(set(extra) & known)
    if clash:
        raise InvalidEnvelope(f"extra may not contain envelope fields: {clash}")
    return freeze(extra)  # type: ignore[return-value]


def _optional_text(value: object, label: str, *, nonempty: bool = False) -> None:
    if value is not None and (type(value) is not str or (nonempty and not value.strip())):
        raise InvalidEnvelope(f"{label} must be {'nonempty ' if nonempty else ''}text")


@dataclass(frozen=True)
class RecordEnvelope:
    """One definition revision (pinned ``SourceRef``) or campaign instance (``InstanceRef``)."""

    address: Address
    payload: object = None
    name: str | None = None
    owner: str | None = None
    derived_from: str | None = None
    base: str | None = None
    base_etag: str | None = None
    extra: Mapping[str, object] = field(default_factory=_empty)

    def __post_init__(self) -> None:
        if isinstance(self.address, SourceRef):
            if not self.address.pinned:
                raise InvalidEnvelope(f"a definition record is one pinned revision, not {self.address}")
            present = [label for label in _INSTANCE_ONLY if getattr(self, label) is not None]
            if present:
                raise InvalidEnvelope(f"only campaign instances carry {present}")
        elif not isinstance(self.address, InstanceRef):
            raise InvalidEnvelope("address must be a SourceRef or InstanceRef")
        for label in ("derived_from", "base"):  # accept refs, keep text
            value = getattr(self, label)
            if isinstance(value, SourceRef):
                object.__setattr__(self, label, str(value))
        _optional_text(self.name, "name")
        _optional_text(self.owner, "owner", nonempty=True)
        for label in ("derived_from", "base", "base_etag"):
            _optional_text(getattr(self, label), label)
        if self.base_etag is not None and self.base is None:
            raise InvalidEnvelope("base_etag needs a base")
        object.__setattr__(self, "payload", freeze(self.payload))
        object.__setattr__(self, "extra", _frozen_extra(self.extra, _RECORD_FIELDS))

    @property
    def is_definition(self) -> bool:
        return isinstance(self.address, SourceRef)

    def check_writable(self) -> None:
        """What only a write must prove: references parse and values read back."""
        if self.derived_from is not None:
            _parse_source_ref(self.derived_from, "derived_from")
        if self.base is not None and not _parse_source_ref(self.base, "base").pinned:
            raise InvalidEnvelope(f"base must be a pinned revision: {self.base}")
        if self.base_etag is not None and not is_etag(self.base_etag):
            raise InvalidEnvelope("base_etag must be 'sha256:' and 64 lowercase hex digits")
        check_writable(self.payload)
        check_writable(self.extra)


def _parse_source_ref(text: str, label: str) -> SourceRef:
    try:
        return SourceRef.parse(text)
    except ValueError as error:
        raise InvalidEnvelope(f"{label}: {error}") from None


@dataclass(frozen=True)
class LinkSpec:
    """A link as a manifest states it; ``declared_by`` is the manifest's own source."""

    subject: str
    relation: str
    object: str
    extra: Mapping[str, object] = field(default_factory=_empty)

    def __post_init__(self) -> None:
        for label in ("subject", "relation", "object"):
            value = getattr(self, label)
            if isinstance(value, SourceRef):
                object.__setattr__(self, label, str(value))
            elif type(getattr(self, label)) is not str:
                raise InvalidEnvelope(f"link {label} must be text")
        object.__setattr__(self, "extra", _frozen_extra(self.extra, _LINK_FIELDS))


@dataclass(frozen=True)
class SourceManifest:
    """A source's owner, declared heads and links. Heads are keyed ``<kind>/<entity_id>``."""

    source: str
    owner: str
    title: str | None = None
    heads: Mapping[str, str] = field(default_factory=_empty)
    links: tuple[LinkSpec, ...] = ()
    extra: Mapping[str, object] = field(default_factory=_empty)

    def __post_init__(self) -> None:
        try:
            validate_source_id(self.source)
        except ValueError as error:
            raise InvalidEnvelope(str(error)) from None
        _optional_text(self.owner, "owner", nonempty=True)
        if self.owner is None:
            raise InvalidEnvelope("a source needs an owner")
        _optional_text(self.title, "title")
        if not isinstance(self.heads, Mapping) or not all(
                type(key) is str and type(value) is str for key, value in self.heads.items()):
            raise InvalidEnvelope("heads must map '<kind>/<entity_id>' text to revision text")
        object.__setattr__(self, "heads", MappingProxyType(dict(self.heads)))
        object.__setattr__(self, "links", tuple(self.links))
        if not all(isinstance(link, LinkSpec) for link in self.links):
            raise InvalidEnvelope("links must be LinkSpec values")
        object.__setattr__(self, "extra", _frozen_extra(self.extra, _SOURCE_FIELDS))

    def head_refs(self) -> dict[SourceRef, str]:
        """Heads as unpinned refs of this source; raises ``ValueError`` for a bad key."""
        heads = {}
        for key, revision in self.heads.items():
            kind, separator, entity_id = key.partition("/")
            if not separator:
                raise ValueError(f"head key must be '<kind>/<entity_id>': {key!r}")
            validate_segment(revision, "head revision")
            heads[SourceRef(self.source, kind, entity_id)] = revision
        return heads

    def check_writable(self) -> None:
        try:
            self.head_refs()
        except ValueError as error:
            raise InvalidEnvelope(str(error)) from None
        for link in self.links:
            problem = link_problem(link, self.source)
            if problem is not None:
                raise InvalidEnvelope(problem[1])
            check_writable(link.extra)
        check_writable(self.extra)


def link_problem(link: LinkSpec, declared_by: str) -> tuple[str, str] | None:
    """``(code, detail)`` when a link is unusable: ``ownership`` or ``bad_link``."""
    if "declared_by" in link.extra:
        return "ownership", "a link may not name its declarer; it is the manifest's own source"
    try:
        subject, obj = SourceRef.parse(link.subject), SourceRef.parse(link.object)
    except ValueError as error:
        return "bad_link", f"unparsable link endpoint: {error}"
    if subject.source != declared_by:
        return "ownership", f"{declared_by} may only declare links whose subject is its own: {subject}"
    if link.relation not in RELATIONS:
        return "bad_link", f"unknown relation {link.relation!r}"
    if subject.entity_key == obj.entity_key:
        return "bad_link", f"a definition cannot be linked to itself: {subject}"
    if link.relation in DIRECTED_RELATIONS and not (subject.pinned and obj.pinned):
        return "bad_link", f"{link.relation} must pin both revisions"
    return None


# -- documents ---------------------------------------------------------------

def record_document(envelope: RecordEnvelope, previous: str | None) -> dict[str, object]:
    document: dict[str, object] = dict(thaw(envelope.extra))  # type: ignore[arg-type]
    document.update(format=RECORD_FORMAT, address=str(envelope.address), payload=thaw(envelope.payload),
                    previous=previous)
    for label in ("name", "owner", "derived_from", "base", "base_etag"):
        value = getattr(envelope, label)
        if value is not None:
            document[label] = value
    return document


def source_document(manifest: SourceManifest, previous: str | None) -> dict[str, object]:
    document: dict[str, object] = dict(thaw(manifest.extra))  # type: ignore[arg-type]
    document.update(
        format=SOURCE_FORMAT, source=manifest.source, owner=manifest.owner, heads=dict(manifest.heads),
        links=[{**thaw(link.extra), "subject": link.subject, "relation": link.relation,  # type: ignore[dict-item]
                "object": link.object} for link in manifest.links],
        previous=previous)
    if manifest.title is not None:
        document["title"] = manifest.title
    return document


def _format(document: Mapping[str, object], expected: str) -> None:
    value = document.get("format")
    if type(value) is not str:
        raise EnvelopeError("malformed", "missing format")
    if value != expected:
        raise EnvelopeError("unsupported_format", f"format {value!r} is not {expected!r}")


def _previous(document: Mapping[str, object]) -> str | None:
    previous = document.get("previous")
    if previous is not None and not is_etag(previous):
        raise EnvelopeError("malformed", "previous must be null or an etag")
    return previous


def parse_record(document: Mapping[str, object]) -> tuple[RecordEnvelope, str | None]:
    _format(document, RECORD_FORMAT)
    address_text = document.get("address")
    try:
        address = parse_ref(address_text)
    except ValueError as error:
        raise EnvelopeError("malformed", f"address: {error}") from None
    if not isinstance(address, (SourceRef, InstanceRef)):
        raise EnvelopeError("malformed", "a legacy V1 id is not a record address")
    if "payload" not in document:
        raise EnvelopeError("malformed", "missing payload")
    fields = {label: document[label] for label in ("name", "owner", "derived_from", "base", "base_etag")
              if label in document}
    extra = {key: value for key, value in document.items() if key not in _RECORD_FIELDS}
    try:
        envelope = RecordEnvelope(address, document["payload"], extra=extra, **fields)  # type: ignore[arg-type]
    except InvalidEnvelope as error:
        raise EnvelopeError("malformed", str(error)) from None
    return envelope, _previous(document)


def parse_source(document: Mapping[str, object]) -> tuple[SourceManifest, str | None]:
    _format(document, SOURCE_FORMAT)
    raw_links = document.get("links", ())
    if not isinstance(raw_links, (list, tuple)):
        raise EnvelopeError("malformed", "links must be an array")
    links = []
    for raw in raw_links:
        if not isinstance(raw, Mapping) or not all(type(raw.get(key)) is str for key in _LINK_FIELDS):
            raise EnvelopeError("malformed", "a link needs text subject, relation and object")
        links.append(LinkSpec(raw["subject"], raw["relation"], raw["object"],
                              extra={key: value for key, value in raw.items() if key not in _LINK_FIELDS}))
    heads = document.get("heads", {})
    if not isinstance(heads, Mapping):
        raise EnvelopeError("malformed", "heads must be an object")
    try:
        manifest = SourceManifest(
            document.get("source"), document.get("owner"), document.get("title"), heads,  # type: ignore[arg-type]
            tuple(links), extra={key: value for key, value in document.items() if key not in _SOURCE_FIELDS})
    except InvalidEnvelope as error:
        raise EnvelopeError("malformed", str(error)) from None
    return manifest, _previous(document)
