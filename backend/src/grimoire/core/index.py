"""Name-safe definition resolution, explicit definition links and legacy IDs.

The index is a read-only resolver built once from registered sources. It
never matches by name: names only produce *candidates* for a human or caller
to choose between. Unpinned references follow each entity's declared head;
pinned references mean exactly one revision. Registration problems raise an
``IdentityConflict`` naming the sources to exclude, so one broken add-on
cannot make a user's own world unresolvable.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from .identity import IdentityConflict, LegacyId, SourceRef, validate_source_id

Relation = Literal["same_individual", "alternate_portrayal", "derived_variant", "overrides"]
Reason = Literal["missing_source", "missing_entity", "missing_revision"]

SYMMETRIC_RELATIONS = frozenset({"same_individual", "alternate_portrayal"})
DIRECTED_RELATIONS = frozenset({"derived_variant", "overrides"})

EntityKey = tuple[str, str, str]


class Definition(Protocol):
    """Anything with a pinned source-qualified ref and a display name."""

    @property
    def ref(self) -> SourceRef: ...

    @property
    def name(self) -> str: ...


class MissingDefinition(LookupError):
    """A well-formed reference does not resolve; never substitute a namesake."""

    def __init__(self, ref: SourceRef, reason: Reason) -> None:
        super().__init__(f"{reason}: {ref}")
        self.ref = ref
        self.reason = reason


@dataclass(frozen=True, slots=True)
class BrokenReference:
    """Why a reference does not resolve; callers keep the reference verbatim."""

    ref: SourceRef
    reason: Reason


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """A registered world, catalog or add-on and who controls it."""

    source_id: str
    owner: str
    title: str | None = None

    def __post_init__(self) -> None:
        validate_source_id(self.source_id, "source_id")
        if not isinstance(self.owner, str) or not self.owner.strip():
            raise ValueError("a source owner must be a nonempty string")


def _require_ref(value: object, label: str) -> SourceRef:
    if not isinstance(value, SourceRef):
        raise ValueError(f"{label} must be a SourceRef, not {type(value).__name__}")
    return value


@dataclass(frozen=True, slots=True)
class DefinitionLink:
    """An explicit relation between definitions; equal names are no relation."""

    subject: SourceRef
    relation: Relation
    object: SourceRef
    declared_by: str

    def __post_init__(self) -> None:
        _require_ref(self.subject, "link subject")
        _require_ref(self.object, "link object")
        if self.relation not in SYMMETRIC_RELATIONS | DIRECTED_RELATIONS:
            raise ValueError(f"unknown definition relation: {self.relation!r}")
        validate_source_id(self.declared_by, "declared_by")
        if self.relation in DIRECTED_RELATIONS and not self.object.pinned:
            raise IdentityConflict(
                f"{self.relation} must pin the revision it came from: {self.object}",
                (self.declared_by,),
            )

    @property
    def identity(self) -> tuple[object, ...]:
        """Two links with equal identity are duplicates."""
        if self.relation in SYMMETRIC_RELATIONS:
            return (self.relation, frozenset({self.subject.entity_key, self.object.entity_key}))
        return (self.relation, self.subject, self.object)


def _name_key(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


class DefinitionIndex:
    """Immutable resolver over registered sources' definitions and links."""

    def __init__(
        self,
        sources: Iterable[SourceDescriptor],
        definitions: Iterable[Definition],
        heads: Mapping[SourceRef, str] | None = None,
        links: Iterable[DefinitionLink] = (),
    ) -> None:
        self._sources = self._register_sources(sources)
        self._revisions: dict[EntityKey, dict[str, Definition]] = {}
        self._names: dict[str, set[EntityKey]] = {}
        self._register_definitions(definitions)
        self._heads = self._choose_heads(dict(heads or {}))
        self._links = self._register_links(links)

    @staticmethod
    def _register_sources(sources: Iterable[SourceDescriptor]) -> dict[str, SourceDescriptor]:
        registered: dict[str, SourceDescriptor] = {}
        for source in sources:
            if not isinstance(source, SourceDescriptor):
                raise ValueError("sources must be SourceDescriptor values")
            if source.source_id in registered:
                earlier = registered[source.source_id]
                raise IdentityConflict(
                    f"source {source.source_id} is claimed by both {earlier.owner!r} and "
                    f"{source.owner!r}; one must be renamed",
                    (source.source_id,),
                )
            registered[source.source_id] = source
        return registered

    def _register_definitions(self, definitions: Iterable[Definition]) -> None:
        kinds: dict[tuple[str, str], str] = {}
        for definition in definitions:
            ref = _require_ref(definition.ref, "definition ref")
            if ref.source not in self._sources:
                raise IdentityConflict(f"definition from unregistered source: {ref}", (ref.source,))
            if not ref.pinned:
                raise IdentityConflict(f"a definition must be a pinned revision: {ref}", (ref.source,))
            kind = kinds.setdefault((ref.source, ref.entity_id), ref.kind)
            if kind != ref.kind:
                raise IdentityConflict(
                    f"{ref.source} uses entity id {ref.entity_id} for both {kind} and {ref.kind}",
                    (ref.source,),
                )
            revisions = self._revisions.setdefault(ref.entity_key, {})
            if ref.revision in revisions:
                raise IdentityConflict(f"definition registered twice: {ref}", (ref.source,))
            revisions[ref.revision] = definition
            self._names.setdefault(_name_key(definition.name), set()).add(ref.entity_key)

    def _choose_heads(self, declared: dict[SourceRef, str]) -> dict[EntityKey, str]:
        heads: dict[EntityKey, str] = {}
        for key_ref, revision in declared.items():
            key_ref = _require_ref(key_ref, "head key")
            if key_ref.pinned:
                raise IdentityConflict(f"a head must be keyed by an unpinned ref: {key_ref}", (key_ref.source,))
            revisions = self._revisions.get(key_ref.entity_key)
            if revisions is None:
                raise IdentityConflict(f"head declared for unknown entity: {key_ref}", (key_ref.source,))
            if revision not in revisions:
                raise IdentityConflict(
                    f"head of {key_ref} names unregistered revision {revision!r}", (key_ref.source,)
                )
            heads[key_ref.entity_key] = revision
        for key, revisions in self._revisions.items():
            if key in heads:
                continue
            if len(revisions) > 1:
                raise IdentityConflict(
                    f"{SourceRef(*key)} has {len(revisions)} revisions and no declared head", (key[0],)
                )
            heads[key] = next(iter(revisions))
        return heads

    def _register_links(self, links: Iterable[DefinitionLink]) -> tuple[DefinitionLink, ...]:
        accepted: dict[tuple[object, ...], DefinitionLink] = {}
        for link in links:
            if not isinstance(link, DefinitionLink):
                raise ValueError("links must be DefinitionLink values")
            if link.declared_by not in self._sources:
                raise IdentityConflict(f"link declared by unregistered source: {link}", (link.declared_by,))
            if link.subject.entity_key == link.object.entity_key:
                raise IdentityConflict(f"a definition cannot be linked to itself: {link}", (link.declared_by,))
            earlier = accepted.get(link.identity)
            if earlier is not None:
                raise IdentityConflict(
                    f"duplicate link: {link}", (earlier.declared_by, link.declared_by)
                )
            accepted[link.identity] = link
        result = tuple(accepted.values())
        self._reject_derivation_cycles(result)
        return result

    @staticmethod
    def _reject_derivation_cycles(links: tuple[DefinitionLink, ...]) -> None:
        edges: dict[EntityKey, list[DefinitionLink]] = {}
        for link in links:
            if link.relation in DIRECTED_RELATIONS:
                edges.setdefault(link.subject.entity_key, []).append(link)
        done: set[EntityKey] = set()
        path: list[DefinitionLink] = []
        on_path: set[EntityKey] = set()

        def visit(key: EntityKey) -> None:
            on_path.add(key)
            for link in edges.get(key, ()):
                target = link.object.entity_key
                path.append(link)
                if target in on_path:
                    start = next(i for i, step in enumerate(path) if step.subject.entity_key == target)
                    cycle = path[start:]
                    raise IdentityConflict(
                        "derivation cycle: " + ", ".join(str(step.subject) for step in cycle),
                        tuple(step.declared_by for step in cycle),
                    )
                if target not in done:
                    visit(target)
                path.pop()
            on_path.discard(key)
            done.add(key)

        for key in list(edges):
            if key not in done:
                visit(key)

    @property
    def sources(self) -> tuple[SourceDescriptor, ...]:
        return tuple(self._sources.values())

    def _lookup(self, ref: SourceRef) -> Definition | BrokenReference:
        ref = _require_ref(ref, "reference")
        if ref.source not in self._sources:
            return BrokenReference(ref, "missing_source")
        revisions = self._revisions.get(ref.entity_key)
        if revisions is None:
            return BrokenReference(ref, "missing_entity")
        revision = ref.revision if ref.pinned else self._heads[ref.entity_key]
        if revision not in revisions:
            return BrokenReference(ref, "missing_revision")
        return revisions[revision]

    def resolve(self, ref: SourceRef) -> Definition:
        """The exact pinned revision, or the head for an unpinned reference."""
        found = self._lookup(ref)
        if isinstance(found, BrokenReference):
            raise MissingDefinition(found.ref, found.reason)
        return found

    def check(self, ref: SourceRef) -> BrokenReference | None:
        found = self._lookup(ref)
        return found if isinstance(found, BrokenReference) else None

    def check_links(self) -> tuple[tuple[DefinitionLink, BrokenReference], ...]:
        return tuple(
            (link, broken)
            for link in self._links
            for endpoint in (link.subject, link.object)
            if (broken := self.check(endpoint)) is not None
        )

    def find_by_name(self, name: str, kind: str | None = None) -> tuple[SourceRef, ...]:
        """Candidates whose name (in any revision) matches; never a selection."""
        if not isinstance(name, str):
            raise ValueError("a name must be text")
        keys = self._names.get(_name_key(name), set())
        return tuple(sorted(
            (SourceRef(*key) for key in keys if kind is None or key[1] == kind), key=str
        ))

    def links_for(self, ref: SourceRef) -> tuple[DefinitionLink, ...]:
        key = _require_ref(ref, "reference").entity_key
        return tuple(
            link for link in self._links
            if key in (link.subject.entity_key, link.object.entity_key)
        )


class UnmappedLegacyId(LookupError):
    """No reviewed mapping exists; never guess from a slug or name."""

    def __init__(self, legacy_id: LegacyId) -> None:
        super().__init__(f"no V2 mapping for {legacy_id}")
        self.legacy_id = legacy_id


class LegacyIdMap:
    """Reviewed V1 record → pinned V2 definition mapping (many-to-one allowed)."""

    def __init__(self, pairs: Iterable[tuple[LegacyId, SourceRef]]) -> None:
        self._targets: dict[LegacyId, SourceRef] = {}
        for legacy_id, target in pairs:
            if not isinstance(legacy_id, LegacyId):
                raise ValueError("legacy mappings need LegacyId keys")
            target = _require_ref(target, "legacy mapping target")
            if not target.pinned:
                raise IdentityConflict(
                    f"{legacy_id} must map to the imported revision, not {target}", (target.source,)
                )
            earlier = self._targets.setdefault(legacy_id, target)
            if earlier != target:
                raise IdentityConflict(
                    f"{legacy_id} maps to both {earlier} and {target}", (earlier.source, target.source)
                )

    def resolve(self, legacy_id: LegacyId) -> SourceRef:
        try:
            return self._targets[legacy_id]
        except KeyError:
            raise UnmappedLegacyId(legacy_id) from None
