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

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.ref, self.reason))


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
        if not isinstance(self.relation, str) or self.relation not in SYMMETRIC_RELATIONS | DIRECTED_RELATIONS:
            raise ValueError(f"unknown definition relation: {self.relation!r}")
        validate_source_id(self.declared_by, "declared_by")
        if self.relation in DIRECTED_RELATIONS and not (self.subject.pinned and self.object.pinned):
            raise IdentityConflict(
                f"{self.relation} must pin both the derived revision and its base: {self}",
                (self.declared_by,),
            )

    def __str__(self) -> str:
        return f"{self.subject} {self.relation} {self.object} (declared by {self.declared_by})"

    @property
    def identity(self) -> tuple[object, ...]:
        """Links with equal identity state the same relation."""
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
            ref = _require_ref(getattr(definition, "ref", None), "definition ref")
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
            if not isinstance(getattr(definition, "name", None), str):
                raise IdentityConflict(f"definition {ref} has no text name", (ref.source,))
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
            if not isinstance(revision, str) or revision not in revisions:
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
        accepted: list[DefinitionLink] = []
        seen: set[tuple[object, ...]] = set()
        for link in links:
            if not isinstance(link, DefinitionLink):
                raise ValueError("links must be DefinitionLink values")
            if link.declared_by not in self._sources:
                raise IdentityConflict(f"link declared by unregistered source: {link}", (link.declared_by,))
            if link.subject.entity_key == link.object.entity_key:
                raise IdentityConflict(f"a definition cannot be linked to itself: {link}", (link.declared_by,))
            # Another source stating the same relation is agreement, kept as provenance.
            claim = (link.identity, link.declared_by)
            if claim in seen:
                raise IdentityConflict(f"duplicate link: {link}", (link.declared_by,))
            seen.add(claim)
            accepted.append(link)
        self._reject_derivation_cycles([link for link in accepted if link.relation in DIRECTED_RELATIONS])
        return tuple(accepted)

    @staticmethod
    def _reject_derivation_cycles(directed: list[DefinitionLink]) -> None:
        """Blame only the link, in registration order, that first closes a cycle.

        Nodes are pinned revisions, so forks that sync across revisions are
        not a cycle. A binary search over prefixes keeps this O(n log n).
        """
        if not _has_cycle(directed):
            return
        low, high = 1, len(directed)  # smallest prefix length containing a cycle
        while low < high:
            middle = (low + high) // 2
            if _has_cycle(directed[:middle]):
                high = middle
            else:
                low = middle + 1
        closing = directed[low - 1]
        raise IdentityConflict(f"derivation cycle closed by: {closing}", (closing.declared_by,))

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


def _has_cycle(directed: list[DefinitionLink]) -> bool:
    """Kahn's algorithm over pinned-revision nodes; iterative, so no recursion limit."""
    targets: dict[SourceRef, list[SourceRef]] = {}
    incoming: dict[SourceRef, int] = {}
    for link in directed:
        targets.setdefault(link.subject, []).append(link.object)
        incoming.setdefault(link.subject, 0)
        incoming[link.object] = incoming.get(link.object, 0) + 1
    ready = [node for node, count in incoming.items() if count == 0]
    removed = 0
    while ready:
        node = ready.pop()
        removed += 1
        for target in targets.get(node, ()):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    return removed < len(incoming)


class UnmappedLegacyId(LookupError):
    """No reviewed mapping exists; never guess from a slug or name."""

    def __init__(self, legacy_id: LegacyId) -> None:
        super().__init__(f"no V2 mapping for {legacy_id}")
        self.legacy_id = legacy_id

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.legacy_id,))


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
                # The reviewed mapping is at fault, not either target source.
                raise IdentityConflict(f"{legacy_id} maps to both {earlier} and {target}")

    def resolve(self, legacy_id: LegacyId) -> SourceRef:
        try:
            return self._targets[legacy_id]
        except KeyError:
            raise UnmappedLegacyId(legacy_id) from None
