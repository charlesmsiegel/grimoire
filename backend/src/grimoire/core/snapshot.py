"""A frozen, world-optional view of selected sources and captured instances for one operation (022).

A ``SourceSelection`` is what a chat or scene chose: sources under roles (``character``, ``persona``,
``scenario``, ``lorebook``) and an optional world. ``world=None`` is valid; a world is provenance
only, and **nothing in a world's catalog resolves unless it is explicitly selected**.

An ``EffectiveSnapshot`` resolves every selected source once, against one immutable
``DefinitionIndex``, when it is built. That is the revision boundary: every later call answers from
the same revisions and the same captured instance values, so a turn never sees a half-updated
source, actor or binding. A new load is a new snapshot. Nothing here reads files or calls a model.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from .entity import EffectiveEntity, EntityDefinition, EntityInstance, compose_instance
from .identity import InstanceRef, SourceRef, validate_segment, validate_source_id
from .index import BrokenReference, Definition, DefinitionIndex, MissingDefinition, name_key
from .portrayal import ChainNode, EffectiveDefinition, Explain, Notice, PortrayalError, compose, derivation_chain

Decode = Callable[[Definition], EntityDefinition]
EtagOf = Callable[[Definition], "str | None"]


def _fail(message: str, reason: str, ref: SourceRef | None = None) -> PortrayalError:
    return PortrayalError(message, reason, ref)


@dataclass(frozen=True, slots=True)
class SelectedSource:
    """One selected source: the reference exactly as chosen, its role, and what it meant when chosen.

    ``resolved_revision`` is the revision an unpinned (head-following) selection meant when it was
    introduced; ``actor`` is the campaign actor this selection created or reused, if any.
    """

    ref: SourceRef
    role: str
    resolved_revision: str | None = None
    actor: InstanceRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ref, SourceRef):
            raise _fail("a selected source needs a SourceRef", "invalid_selection")
        try:
            validate_segment(self.role, "role")
            if self.resolved_revision is not None:
                validate_segment(self.resolved_revision, "resolved_revision")
        except ValueError as error:
            raise _fail(str(error), "invalid_selection", self.ref) from None
        if self.ref.pinned and self.resolved_revision not in (None, self.ref.revision):
            raise _fail(f"{self.ref} is pinned; it cannot have resolved to {self.resolved_revision}",
                        "invalid_selection", self.ref)
        if self.actor is not None and not isinstance(self.actor, InstanceRef):
            raise _fail("a selected actor must be an InstanceRef", "invalid_selection", self.ref)


@dataclass(frozen=True, slots=True)
class SourceSelection:
    """What one chat or scene selected; ``world=None`` is a deliberate, valid value."""

    world: str | None = None
    sources: tuple[SelectedSource, ...] = ()

    def __post_init__(self) -> None:
        if self.world is not None:
            try:
                validate_source_id(self.world, "world")
            except ValueError as error:
                raise _fail(str(error), "invalid_selection") from None
        try:
            sources = tuple(self.sources)
        except TypeError:  # None, or one SelectedSource passed bare: malformed, like any other element
            raise _fail("sources must be a collection of SelectedSource values", "invalid_selection") from None
        object.__setattr__(self, "sources", sources)
        keys: set[tuple[str, tuple[str, str, str]]] = set()
        actors: set[InstanceRef] = set()
        for source in sources:
            if not isinstance(source, SelectedSource):
                raise _fail("sources must be SelectedSource values", "invalid_selection")
            key = (source.role, source.ref.entity_key)
            if key in keys:
                raise _fail(f"{source.ref.unpinned()} is selected twice as {source.role}",
                            "conflicting_selection", source.ref)
            keys.add(key)
            if source.actor is not None:
                if source.actor in actors:
                    raise _fail(f"actor {source.actor} belongs to two selections", "conflicting_selection",
                                source.ref)
                actors.add(source.actor)


@dataclass(frozen=True, slots=True)
class Resolution:
    """What one selected source means in this snapshot: a pinned revision, or why it is broken."""

    source: SelectedSource
    resolved: SourceRef | None
    broken: BrokenReference | None = None
    notices: tuple[Notice, ...] = ()


def _default_etag(definition: Definition) -> str | None:
    etag = getattr(definition, "etag", None)
    return etag if isinstance(etag, str) else None


class EffectiveSnapshot:
    """Frozen answers for one operation over an immutable index, a selection and captured instances."""

    def __init__(self, index: DefinitionIndex, selection: SourceSelection, *, decode: Decode | None = None,
                 etag_of: EtagOf | None = None, explain: Explain | None = None,
                 instances: Iterable[tuple[EntityInstance, str | None]] = ()) -> None:
        if not isinstance(index, DefinitionIndex):
            raise TypeError(f"expected a DefinitionIndex, not {type(index).__name__}")
        if not isinstance(selection, SourceSelection):
            raise TypeError(f"expected a SourceSelection, not {type(selection).__name__}")
        self._index = index
        self._selection = selection
        self._decode = decode
        self._etag_of = etag_of or _default_etag
        self._explain = explain
        self._resolutions = tuple(self._resolve_selected(source) for source in selection.sources)
        self._instances: dict[InstanceRef, tuple[EntityInstance, str | None]] = {}
        for instance, etag in instances:
            if not isinstance(instance, EntityInstance):
                raise TypeError(f"expected an EntityInstance, not {type(instance).__name__}")
            if instance.ref in self._instances:
                raise _fail(f"instance {instance.ref} captured twice", "conflicting_instance")
            self._instances[instance.ref] = (instance, etag)
        self._composed: dict[SourceRef, EffectiveDefinition | PortrayalError] = {}
        self._chains: dict[SourceRef, tuple[ChainNode, ...] | PortrayalError] = {}

    def _resolve_selected(self, source: SelectedSource) -> Resolution:
        broken = self._index.check(source.ref)
        if broken is not None:
            return Resolution(source, None, broken)
        resolved = self._index.resolve(source.ref).ref
        notices: tuple[Notice, ...] = ()
        if not source.ref.pinned and source.resolved_revision not in (None, resolved.revision):
            notices = (Notice("binding_drift", source.ref, f"{source.resolved_revision} -> {resolved.revision}"),)
        return Resolution(source, resolved, None, notices)

    # -- what was selected ------------------------------------------------------------------------

    @property
    def selection(self) -> SourceSelection:
        return self._selection

    @property
    def world(self) -> str | None:
        return self._selection.world

    def sources(self) -> tuple[Resolution, ...]:
        """Every selected source with its resolution in this snapshot, in selection order."""
        return self._resolutions

    # -- definitions ------------------------------------------------------------------------------

    def _read(self, ref: SourceRef) -> tuple[EntityDefinition, str | None]:
        raw = self._index.resolve(ref)
        definition = self._decode(raw) if self._decode is not None else raw
        if not isinstance(definition, EntityDefinition):
            raise _fail(f"{ref} is not an entity definition", "not_entity_definition", ref)
        return definition, self._etag_of(raw)

    def _compose(self, pinned: SourceRef) -> EffectiveDefinition:
        found = self._composed.get(pinned)
        if found is None:
            try:
                found = compose(pinned, self._index, self._read, self._explain)
            except PortrayalError as error:
                found = error
            self._composed[pinned] = found
        if isinstance(found, PortrayalError):
            raise PortrayalError(str(found), found.reason, found.ref, found.detail)
        return found

    def _chain(self, pinned: SourceRef) -> tuple[ChainNode, ...] | PortrayalError:
        """The selection's pinned ancestry, independent of whether its fields compose (memoized)."""
        if pinned not in self._chains:
            try:
                self._chains[pinned] = derivation_chain(pinned, self._index, self._read, self._explain)
            except PortrayalError as error:
                # a lorebook or scenario has no ancestry at all: that is not a failed walk
                leaf_is_not_entity = error.reason == "not_entity_definition" and error.ref == pinned
                self._chains[pinned] = (ChainNode(pinned),) if leaf_is_not_entity else error
        return self._chains[pinned]

    def _in_selected_chain(self, ref: SourceRef, role: str | None) -> bool:
        """Whether ``ref`` is an ancestor (never the leaf) of a selection of ``role``.

        If it is not, but some such selection's ancestry could not be walked, that is the honest
        answer: ``ancestry_unavailable`` (detail: the walk's reason), never a false ``not_selected``.
        """
        failed: PortrayalError | None = None
        for resolution in self._resolutions:
            if resolution.resolved is None or (role is not None and resolution.source.role != role):
                continue
            chain = self._chain(resolution.resolved)
            if isinstance(chain, PortrayalError):
                failed = failed or chain
            elif any(node.ref == ref for node in chain[1:]):
                return True
        if failed is not None:
            detail = failed.reason if failed.detail is None else f"{failed.reason}/{failed.detail}"
            raise PortrayalError(f"{ref} may be an ancestor of {failed.ref}, whose ancestry cannot be walked "
                                 f"({detail})", "ancestry_unavailable", failed.ref, detail)
        return False

    def _pinned(self, ref: SourceRef, role: str | None, ancestors: bool = True) -> SourceRef:
        if not isinstance(ref, SourceRef):
            raise TypeError(f"expected a SourceRef, not {type(ref).__name__}")
        candidates = [resolution for resolution in self._resolutions
                      if resolution.source.ref.entity_key == ref.entity_key
                      and (role is None or resolution.source.role == role)]
        other_roles = not candidates and role is not None and any(
            resolution.source.ref.entity_key == ref.entity_key for resolution in self._resolutions)
        if other_roles and not (ref.pinned and ancestors and self._in_selected_chain(ref, role)):
            raise _fail(f"{ref.unpinned()} is selected, but not as {role}", "role_not_selected", ref)
        if ref.pinned:
            if any(resolution.resolved == ref for resolution in candidates):
                return ref
            for resolution in candidates:
                # A broken head-following selection selected no revision at all. A head always names a
                # registered revision, so it is broken only because its source or entity is absent,
                # and then every revision of it is missing too: ``revision_not_selected`` would wrongly
                # claim the snapshot chose some other revision.
                if resolution.broken is not None and (resolution.source.ref == ref
                                                      or not resolution.source.ref.pinned):
                    raise MissingDefinition(resolution.broken.ref, resolution.broken.reason)
            if ancestors and self._in_selected_chain(ref, role):
                return ref
            if candidates:
                raise _fail(f"{ref} is not the revision this snapshot selected", "revision_not_selected", ref)
            raise _fail(f"{ref.unpinned()} is not selected", "not_selected", ref)
        if not candidates:
            raise _fail(f"{ref} is not selected", "not_selected", ref)
        if len(candidates) > 1:
            raise _fail(f"{ref} is selected under several roles; name one", "ambiguous_selection", ref)
        resolution = candidates[0]
        if resolution.broken is not None:
            raise MissingDefinition(resolution.broken.ref, resolution.broken.reason)
        return resolution.resolved  # type: ignore[return-value]

    def resolve(self, ref: SourceRef, role: str | None = None) -> Definition:
        """The raw definition of a selected source at its resolved revision, or of a pinned chain member.

        Raw ancestry is for merge owners (voice, art): it still holds content a portrayal cleared.
        Prompt assembly uses ``effective``.
        """
        return self._index.resolve(self._pinned(ref, role))

    def effective(self, ref: SourceRef, role: str | None = None) -> EffectiveDefinition:
        """The effective definition of a **selected** revision; ancestors are reachable only raw."""
        found = self._compose(self._pinned(ref, role, ancestors=False))
        return found if found.requested == ref else replace(found, requested=ref)

    def find_by_name(self, name: str, kind: str | None = None) -> tuple[SourceRef, ...]:
        """Selected entities whose **selected revision's** name matches: candidates, never a selection."""
        wanted = name_key(name)
        if not wanted:
            return ()  # a blank query names nothing
        found = {resolution.source.ref.unpinned() for resolution in self._resolutions
                 if resolution.resolved is not None and (kind is None or resolution.resolved.kind == kind)
                 and self._matches(resolution.resolved, wanted)}
        return tuple(sorted(found, key=str))

    def _matches(self, pinned: SourceRef, wanted: str) -> bool:
        found = self._name_of(pinned)
        return found is not None and name_key(found) == wanted

    def _name_of(self, pinned: SourceRef) -> str | None:
        """An entity's decoded (authored) name; the raw record name only for a non-entity record."""
        try:
            return self._read(pinned)[0].name
        except PortrayalError as error:
            if error.reason != "not_entity_definition":
                return None  # an entity this version cannot decode matches no name, not its envelope's
            return self._index.resolve(pinned).name

    # -- instances --------------------------------------------------------------------------------

    def instance(self, ref: InstanceRef) -> EntityInstance:
        return self._captured(ref)[0]

    def instance_etag(self, ref: InstanceRef) -> str | None:
        return self._captured(ref)[1]

    def _captured(self, ref: InstanceRef) -> tuple[EntityInstance, str | None]:
        if not isinstance(ref, InstanceRef):
            raise TypeError(f"expected an InstanceRef, not {type(ref).__name__}")
        found = self._instances.get(ref)
        if found is None:
            raise _fail(f"{ref} is not captured in this snapshot", "unknown_instance")
        return found

    def instance_notices(self, ref: InstanceRef) -> tuple[Notice, ...]:
        """What a consumer should know about a captured instance's pinned source.

        - ``instance_source_drift``: it pins a revision **missing** from this index, which is how a torn
          load (an instance read after a newer definition the index did not see) is reported;
        - ``actor_rebound``: it pins another revision that exists; 107 rebinding reuses an actor
          without rewriting its source, so this is ordinary, and composition follows the selection.
        """
        instance = self.instance(ref)
        owner = next((r for r in self._resolutions if r.source.actor == ref), None)
        source = instance.source
        if owner is None or owner.resolved is None or source is None or not source.pinned:
            return ()
        if source.entity_key != owner.resolved.entity_key or source == owner.resolved:
            return ()
        detail = f"{source.revision} -> {owner.resolved.revision}"
        if self._index.check(source) is not None:
            return (Notice("instance_source_drift", source, f"{detail} (missing_revision)"),)
        return (Notice("actor_rebound", source, detail),)

    def entity(self, ref: InstanceRef) -> EffectiveEntity:
        """The captured instance composed over its selection's effective definition.

        The actor is matched to a selection by ``SelectedSource.actor``, never by source text. An
        actor no selection names (a detached one) is ``not_selected``, unless it is a campaign
        original with no source. ``EntityError`` from composing the instance propagates.
        """
        instance = self.instance(ref)
        owners = [resolution for resolution in self._resolutions if resolution.source.actor == ref]
        if not owners:
            if instance.source is None:
                return compose_instance(instance, None)
            raise _fail(f"{ref} belongs to no selected source", "not_selected", instance.source)
        resolution = owners[0]
        if instance.source is None or instance.source.entity_key != resolution.source.ref.entity_key:
            raise _fail(f"{ref} is selected as {resolution.source.ref} but its source is {instance.source}",
                        "actor_source_mismatch", resolution.source.ref)
        if resolution.broken is not None:
            raise MissingDefinition(resolution.broken.ref, resolution.broken.reason)
        return compose_instance(instance, self._compose(resolution.resolved))  # type: ignore[arg-type]


__all__ = ["EffectiveSnapshot", "Resolution", "SelectedSource", "SourceSelection"]
