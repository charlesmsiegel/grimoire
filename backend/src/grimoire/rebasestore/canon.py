"""A chat's reviewed actors proposed as a new reusable world source (042).

``CanonProposer.propose`` reads one ``LibraryView``: the chat, its roster and what each picked actor
composes to in that chat. It returns a ``CanonProposal`` whose ``report`` lists, per pick, the new
definition, the base it adapts and any same-named definition elsewhere (reported, never merged or
linked); ``preview`` shows the exact bytes; ``commit`` writes them.

Only new files under ``sources/<world>/`` are written: the manifest and one revision ``r1`` per pick.
The chat, its scope, its actors and every record of their history are read, never written. What a
pick becomes is ``grimoire.rebase.canon_definition``. Attaching the new world to a scope is 107's
``ChatLibrary.attach_world``, which changes no id. No model is called.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from grimoire.chats import ChatCodecError
from grimoire.core.entity import EntityDefinition
from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_to_payload
from grimoire.core.identity import ChatRef, InstanceRef, SourceRef, validate_segment, validate_source_id
from grimoire.core.portrayal import compose
from grimoire.effective import chat_snapshot, explainer
from grimoire.ledgers import security_source_ids
from grimoire.rebase import RebaseError, canon_definition
from grimoire.store import (
    CommitResult, LibraryStore, LinkSpec, NameCollision, RecordEnvelope, RecordNotFound, SourceManifest, StaleWrite,
    StoreError,
)
from grimoire.store.layout import address_parts, definition_parts, manifest_parts, name_key

from .rebase import _reader

#: picks in one proposal
MAX_PICKS = 64
#: the revision every promoted definition starts at
FIRST_REVISION = "r1"
WORLD = "world"


@dataclass(frozen=True, slots=True)
class CanonPick:
    """One roster actor of the chat, and the entity id its definition gets in the new world."""

    actor: InstanceRef
    entity_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.actor, InstanceRef):
            raise TypeError(f"a pick's actor must be an InstanceRef, not {type(self.actor).__name__}")
        validate_segment(self.entity_id, "entity_id")


@dataclass(frozen=True)
class CanonEntry:
    """What one pick becomes. ``base`` is the revision a portrayal adapts (``None``: standalone)."""

    pick: CanonPick
    definition: EntityDefinition
    base: SourceRef | None
    #: other definitions whose name matches (any revision): never merged, linked or replaced
    same_name: tuple[SourceRef, ...] = ()


@dataclass(frozen=True)
class CanonReport:
    world: str
    chat: ChatRef
    entries: tuple[CanonEntry, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        """Every file the proposal writes, all under ``sources/<world>/``."""
        return ("/".join(manifest_parts(self.world)),
                *("/".join(definition_parts(entry.definition.ref)) for entry in self.entries))


@dataclass
class CanonProposal:
    """One store transaction, not yet committed: ``preview`` for a human, then ``commit``."""

    store: LibraryStore
    handle: object
    report: CanonReport
    puts: tuple[RecordEnvelope | SourceManifest, ...]
    #: the chat and each picked actor, at the etag the proposal was made from
    reads: tuple[tuple[ChatRef | InstanceRef, str], ...]
    _committed: bool = field(default=False, repr=False)

    def _transaction(self):  # type: ignore[no-untyped-def]
        transaction = self.store.transaction()
        for envelope in self.puts:
            transaction.put(envelope, None)
        return transaction

    def preview(self) -> dict[str, bytes]:
        """The exact bytes each new file will hold, keyed by logical path."""
        return self._transaction().preview()

    def commit(self, approvals: Mapping[str, str] | None = None) -> CommitResult:
        """Write exactly the previewed files, or nothing.

        ``StaleWrite`` when the world appeared, a security-ledger grant came to name it, or the chat
        or a picked actor changed since the proposal; the store's authorization applies.
        """
        with self.store.lock:
            if self._committed:
                raise StoreError("this proposal was already committed")
            world = self.report.world
            manifest = "/".join(manifest_parts(world))
            moved: list[tuple[str, str]] = []
            if self.store.check_source(world) != "missing_source" or name_key(world) in security_source_ids(self.store):
                moved.append((manifest, "changed"))
            for ref, etag in self.reads:
                try:
                    current = self.store.read(ref).etag  # type: ignore[union-attr]
                except RecordNotFound:
                    current = None
                if current != etag:
                    moved.append(("/".join(address_parts(ref)), "absent" if current is None else "changed"))
            if moved:
                raise StaleWrite(moved)
            result = self._transaction().commit(self.handle, approvals)
            self._committed = True
        return result


def _fail(message: str, reason: str) -> RebaseError:
    return RebaseError(message, reason)


class CanonProposer:
    """Proposals of chat-authored canon over one open, writable ``LibraryStore``.

    Trusted application code: it is never handed to a worker. ``owner`` is the new world's owner (a
    human principal).
    """

    def __init__(self, store: LibraryStore, handle: object, *, owner: str) -> None:
        self.store = store
        self.handle = handle
        self._owner = owner

    def propose(self, chat: ChatRef, world: str, picks: Sequence[CanonPick], title: str | None = None
                ) -> CanonProposal:
        """The new world ``world`` with one definition per pick, or ``RebaseError``.

        Refusals: ``no_picks``/``too_many_picks``, ``duplicate_pick`` (an actor or entity id twice),
        ``not_a_world``, ``world_exists`` (any source there, or a security-ledger grant naming it),
        ``definition_exists`` (a stray revision file where a new one would go), ``missing_chat``,
        ``undecodable_chat``, ``not_in_chat``. What the chat's snapshot refuses for an actor (a missing
        or undecodable one, a broken binding) propagates.
        """
        if not isinstance(chat, ChatRef):
            raise TypeError(f"expected a ChatRef, not {type(chat).__name__}")
        picks = tuple(picks)
        if not all(isinstance(pick, CanonPick) for pick in picks):
            raise TypeError("picks must be CanonPick values")
        if not picks:
            raise _fail("a proposal needs at least one actor", "no_picks")
        if len(picks) > MAX_PICKS:
            raise _fail(f"a proposal holds at most {MAX_PICKS} actors", "too_many_picks")
        if len({pick.actor for pick in picks}) != len(picks) or len({pick.entity_id for pick in picks}) != len(picks):
            raise _fail("each actor and each entity id is picked once", "duplicate_pick")
        try:
            validate_source_id(world, "world")
        except ValueError as error:
            raise _fail(str(error), "not_a_world") from None
        if world.split(":", 1)[0] != WORLD or ":" not in world:
            raise _fail(f"{world} is not a world source (world:<id>)", "not_a_world")
        found = self.store.check_source(world)
        if found != "missing_source" or name_key(world) in security_source_ids(self.store):
            # a grant outlives a hand-deleted folder and would cover a new source given its id
            raise _fail(f"{world} already exists, or its id is still named by a grant", "world_exists")
        view = self.store.load()
        try:
            captured = chat_snapshot(view, chat)
        except RecordNotFound:
            raise _fail(f"{chat} does not exist", "missing_chat") from None
        except ChatCodecError:
            raise _fail(f"{chat} cannot be interpreted by this version", "undecodable_chat") from None
        roster = {entry.actor for entry in captured.chat.roster}
        snapshot = captured.snapshot
        reader, explain = _reader(view), explainer(view)
        entries: list[CanonEntry] = []
        reads: list[tuple[ChatRef | InstanceRef, str]] = [(chat, captured.chat_etag)]
        for pick in picks:
            if pick.actor not in roster:
                raise _fail(f"{pick.actor} is not on {chat}'s roster", "not_in_chat")
            entity = snapshot.entity(pick.actor)
            instance = snapshot.instance(pick.actor)
            etag = snapshot.instance_etag(pick.actor)
            if etag is None:  # captured instances always carry their file's etag; refuse rather than guess
                raise _fail(f"{pick.actor}'s file etag is unknown", "undecodable_actor")
            reads.append((pick.actor, etag))
            ref = SourceRef(world, pick.actor.kind, pick.entity_id, FIRST_REVISION)
            base: SourceRef | None = None
            nature = "individual"
            if instance.source is not None and instance.individuality is None:
                owner = next(resolution for resolution in snapshot.sources() if resolution.source.actor == pick.actor)
                base = owner.resolved
                nature = compose(base, view.index, reader, explain).nature  # type: ignore[arg-type]
            # an individualized template actor (024) is an individual now: a standalone definition, never
            # a derived template, since a portrayal keeps its base's nature
            definition = canon_definition(ref, instance, entity, base, nature,
                                          {"chat": str(chat), "actor": str(pick.actor)})
            same = tuple(other for other in view.index.find_by_name(definition.name, ref.kind)
                         if base is None or other.entity_key != base.entity_key)
            entries.append(CanonEntry(pick, definition, base, same))
        for entry in entries:
            path = "/".join(definition_parts(entry.definition.ref))
            try:
                stray = self.store.read_exact(path)
            except NameCollision:
                stray = b""
            if stray is not None:
                raise _fail(f"{path} already exists outside any source manifest", "definition_exists")
        heads = {f"{entry.definition.ref.kind}/{entry.definition.ref.entity_id}": FIRST_REVISION for entry in entries}
        links = tuple(LinkSpec(entry.definition.ref, "derived_variant", entry.base)
                      for entry in entries if entry.base is not None)
        manifest = SourceManifest(world, self._owner, (title or world)[:256], heads, links)
        puts: list[RecordEnvelope | SourceManifest] = [manifest]
        for entry in entries:
            puts.append(RecordEnvelope(entry.definition.ref, definition_to_payload(entry.definition),
                                       name=entry.definition.name, owner=self._owner, extra={"schema": ENTITY_SCHEMA}))
        report = CanonReport(world, chat, tuple(entries))
        return CanonProposal(self.store, self.handle, report, tuple(puts), tuple(reads))


__all__ = ["FIRST_REVISION", "MAX_PICKS", "CanonEntry", "CanonPick", "CanonProposal", "CanonProposer", "CanonReport"]
