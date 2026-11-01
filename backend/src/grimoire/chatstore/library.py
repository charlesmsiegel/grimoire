"""Store-backed world-optional chats (Sprint 107): start, bind, name and find chats durably.

``ChatLibrary`` is trusted application code and is never handed to a worker. Every mutating
method returns a ``ChatChange``: one store transaction a human can ``preview`` and approve
before ``commit``. The store applies CAS, authorization, link/V1 guards, journal and recovery to
every record the change touches.

New bindings are validated against one source's own files with ``load()``'s rules
(``LibraryStore.check_definition``); the whole library is never loaded for a write. Opening a
chat and building navigation do load the library, and they never fail because a source is gone:
broken references are reported, never rebound.

No model call is made anywhere. Binding a source writes no event, belief or evidence, and a new
actor is ``not_witnessed``: it saw none of the chat's earlier history.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from grimoire.chats import (
    ACTOR_ROLES, CAMPAIGN_CONTINUITY, CHAT_SCHEMA, NOT_WITNESSED, ROLE_KINDS, SCOPE_SCHEMA, ChatCodecError,
    ChatRecord, Launch, NavigationIndex, NewBinding, PlayScope, ScopeEntry, WorldBinding, chat_from_payload,
    chat_to_payload, nav_entry, scope_entry, scope_from_payload, scope_to_payload,
)
from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import ENTITY_SCHEMA, instance_to_payload
from grimoire.core.identity import ChatRef, InstanceRef, ScopeRef, SourceRef, validate_segment
from grimoire.store import (
    LibraryStore, LibraryView, RecordEnvelope, RecordNotFound, ScopeRecord, StaleWrite, StoreError,
)
from grimoire.store.layout import address_parts, classify

#: (role, entity key) -> an existing campaign actor to reuse instead of creating one
ReuseMap = Mapping[tuple[str, tuple[str, str, str]], InstanceRef]
_ALLOCATION_ATTEMPTS = 16


class InvalidBinding(ValueError):
    """A source, world or actor that cannot be bound; nothing was written. ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


@dataclass(frozen=True)
class BrokenBinding:
    """A reference held by a chat or its scope that does not resolve now, kept exactly as written."""

    field: str
    text: str
    reason: str


@dataclass(frozen=True)
class ChatView:
    record: ChatRecord
    etag: str
    scope: PlayScope | None
    scope_etag: str | None
    broken: tuple[BrokenBinding, ...]
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChatCommit:
    etags: Mapping[str, str]
    chat: ChatRef | None
    scope: ScopeRef
    actors: tuple[InstanceRef, ...]


@dataclass
class ChatChange:
    """One store transaction, not yet committed: ``preview`` for a human, then ``commit``."""

    library: ChatLibrary
    puts: tuple[tuple[RecordEnvelope | ScopeRecord, str | None], ...]
    scope: ScopeRef
    chat: ChatRef | None = None
    new_actors: tuple[InstanceRef, ...] = ()
    new_scope: bool = False
    _committed: bool = field(default=False, repr=False)

    def _transaction(self):  # type: ignore[no-untyped-def]
        transaction = self.library.store.transaction()
        for envelope, expected in self.puts:
            transaction.put(envelope, expected)
        return transaction

    def preview(self) -> dict[str, bytes]:
        """The exact bytes each record will hold, keyed by logical path."""
        return self._transaction().preview()

    def commit(self, approvals: Mapping[str, str] | None = None) -> ChatCommit:
        store = self.library.store
        with store.lock:  # nothing can allocate between these checks and the commit
            if self._committed:
                raise StoreError("this change was already committed")
            campaign = self.scope.campaign_id
            if self.new_scope and store.campaign_presence(campaign) != "absent":
                raise StaleWrite([(f"campaigns/{campaign}", "exists")])
            if self.new_actors:
                taken = {ref.instance_id for ref in store.campaign_instances(campaign)}
                clash = [actor for actor in self.new_actors if actor.instance_id in taken]
                if clash:
                    raise StaleWrite([("/".join(address_parts(actor)), "id taken") for actor in clash])
            result = self._transaction().commit(self.library.handle, approvals)
            self._committed = True
        return ChatCommit(result.etags, self.chat, self.scope, self.new_actors)


class ChatLibrary:
    """Durable world-optional chats over one open, writable ``LibraryStore``."""

    def __init__(self, store: LibraryStore, handle: object, *, clock: Callable[[], str],
                 new_id: Callable[[str], str], owner: str | None = None) -> None:
        self.store = store
        self.handle = handle
        self._clock = clock
        self._new_id = new_id
        self._owner = owner

    # -- reading ------------------------------------------------------------------------------

    def read_chat(self, chat: ChatRef) -> tuple[ChatRecord, str]:
        """The chat and its etag; ``ChatCodecError`` if this version cannot interpret it."""
        record, etag, _ = self._read_chat(chat)
        return record, etag

    def read_scope(self, scope: ScopeRef) -> tuple[PlayScope, str]:
        current, etag, _ = self._read_scope(scope)
        return current, etag

    def _read_chat(self, chat: ChatRef) -> tuple[ChatRecord, str, ScopeRecord]:
        found = self.store.read(chat)
        stored = found.record  # type: ignore[union-attr]
        return chat_from_payload(chat, stored.schema, stored.payload), found.etag, stored

    def _read_scope(self, scope: ScopeRef) -> tuple[PlayScope, str, ScopeRecord]:
        found = self.store.read(scope)
        stored = found.record  # type: ignore[union-attr]
        return scope_from_payload(scope, stored.schema, stored.payload), found.etag, stored

    def open_chat(self, chat: ChatRef) -> ChatView:
        """The chat, its scope and every reference that does not resolve now. Never fails on a missing source."""
        view = self.store.load()
        stored = view.chats.get(chat)
        if stored is None:
            self.store.read(chat)  # raises what is wrong: RecordNotFound, MalformedRecord, UnsupportedFormat ...
            raise RecordNotFound(str(chat))  # it appeared since the load: never report a stale view
        record = chat_from_payload(chat, stored.record.schema, stored.record.payload)
        problems: list[str] = []
        scope, scope_etag = None, None
        found = view.scopes.get(chat.scope)
        if found is None and chat.scope in _problem_records(view)[1]:
            problems.append("undecodable_scope")  # the file exists; this version cannot read its envelope
        elif found is None:
            problems.append("orphan_chat")
        else:
            scope_etag = found.etag
            try:
                scope = scope_from_payload(chat.scope, found.record.schema, found.record.payload)
            except ChatCodecError:
                problems.append("undecodable_scope")
        return ChatView(record, stored.etag, scope, scope_etag, tuple(_broken(view, record, scope)), tuple(problems))

    def navigation(self) -> NavigationIndex:
        """Rebuilt from canonical records on every call; nothing is cached."""
        view = self.store.load()
        entries = []
        for ref, stored in view.chats.items():
            try:
                record = chat_from_payload(ref, stored.record.schema, stored.record.payload)
            except ChatCodecError:
                record = None
            entries.append(nav_entry(ref, record))
        scopes = []
        for ref, stored in view.scopes.items():
            try:
                scopes.append(scope_entry(scope_from_payload(ref, stored.record.schema, stored.record.payload)))
            except ChatCodecError:
                scopes.append(ScopeEntry(ref))  # unreadable, but its chats are not orphans
        bad_chats, bad_scopes = _problem_records(view)  # malformed, newer-format or misaddressed files
        entries += [nav_entry(ref, None) for ref in sorted(bad_chats - set(view.chats), key=str)]
        scopes += [ScopeEntry(ref) for ref in sorted(bad_scopes - set(view.scopes), key=str)]
        return NavigationIndex.build(entries, scopes)

    # -- starting a chat ----------------------------------------------------------------------

    def start_chat(self, launch: Launch, *, attach: Iterable[tuple[SourceRef, str]] = (),
                   persona: SourceRef | None = None, label: str | None = None, scope: ScopeRef | None = None,
                   reuse_actors: ReuseMap | None = None) -> ChatChange:
        """A new chat, by default in a new world-less scope; ``scope`` continues an existing campaign."""
        at = self._clock()
        reuse = dict(reuse_actors or {})
        if reuse and scope is None:
            raise InvalidBinding("reusing actors needs an existing scope", "reuse_needs_scope")
        wanted: list[tuple[SourceRef, str]] = []
        if launch.ref is not None:
            wanted.append((launch.ref, launch.kind))
        wanted += list(attach)
        if persona is not None:
            wanted.append((persona, "persona"))
        scope_puts: list[tuple[RecordEnvelope | ScopeRecord, str | None]] = []
        actor_puts: list[tuple[RecordEnvelope | ScopeRecord, str | None]] = []
        if scope is None:
            scope, new_scope, taken = self._allocate_scope(), True, set()
            scope_puts.append((self._scope_record(PlayScope(scope, at)), None))
        else:
            new_scope = False
            if self.store.campaign_presence(scope.campaign_id) != "present":
                raise InvalidBinding(f"{scope} does not exist", "missing_scope")
            try:
                self.store.read(scope)
            except RecordNotFound:  # a campaign from before 107 (or 026): give it a scope record now
                scope_puts.append((self._scope_record(PlayScope(scope, at)), None))
            taken = {ref.instance_id for ref in self.store.campaign_instances(scope.campaign_id)}
        chat = ChatRef(scope.campaign_id, self._id("t"))
        bindings, actors = [], []
        for ref, role in wanted:
            binding, envelope = self._prepare(scope.campaign_id, ref, role, reuse.pop((role, ref.entity_key), None),
                                              taken)
            bindings.append(binding)
            if envelope is not None:
                actors.append(binding.actor)
                actor_puts.append((envelope, None))
        if reuse:
            raise InvalidBinding(f"actors to reuse match no binding: {sorted(map(str, reuse.values()))}",
                                 "reuse_unmatched")
        record = ChatRecord.start(chat, launch, at, tuple(bindings), label)
        puts = (*scope_puts, (self._chat_record(record), None), *actor_puts)
        return ChatChange(self, puts, scope, chat, tuple(actors), new_scope)

    def _allocate_scope(self) -> ScopeRef:
        for _ in range(_ALLOCATION_ATTEMPTS):
            campaign = self._id("q")
            if self.store.campaign_presence(campaign) == "absent":  # a look-alike folder counts as taken
                return ScopeRef(campaign)
        raise StoreError("could not allocate an unused campaign id")

    # -- chat edits -----------------------------------------------------------------------------

    def attach_source(self, chat: ChatRef, ref: SourceRef, role: str, *, expected: str,
                      actor: InstanceRef | None = None) -> ChatChange:
        record, base = self._current(chat, expected)
        taken = self._taken(chat, record)
        binding, envelope = self._prepare(chat.campaign_id, ref, role, actor, taken)
        return self._chat_change(chat, record.attach(binding, self._clock()), base, expected, envelope)

    def detach_source(self, chat: ChatRef, ref: SourceRef, role: str, *, expected: str) -> ChatChange:
        """Tombstone a binding; works for a broken reference (the repair path). Actors stay."""
        record, base = self._current(chat, expected)
        return self._chat_change(chat, record.detach(ref, role, self._clock()), base, expected)

    def set_persona(self, chat: ChatRef, ref: SourceRef | None, *, expected: str,
                    actor: InstanceRef | None = None) -> ChatChange:
        record, base = self._current(chat, expected)
        at = self._clock()
        current = record.persona
        if current is not None:
            if (ref is not None and actor is None and ref.entity_key == current.ref.entity_key
                    and current.actor in self.store.campaign_instances(chat.campaign_id)):
                actor = current.actor  # the same persona (perhaps another revision) keeps its body
            record = record.detach(current.ref, "persona", at)
        envelope = None
        if ref is not None:
            binding, envelope = self._prepare(chat.campaign_id, ref, "persona", actor, self._taken(chat, record))
            record = record.attach(binding, at)
        return self._chat_change(chat, record, base, expected, envelope)

    def set_label(self, chat: ChatRef, label: str | None, *, expected: str) -> ChatChange:
        record, base = self._current(chat, expected)
        return self._chat_change(chat, record.relabel(label, self._clock()), base, expected)

    # -- scope edits ------------------------------------------------------------------------------

    def name_scope(self, scope: ScopeRef, title: str | None, *, expected: str) -> ChatChange:
        """Show (or stop showing) the scope as a named campaign; no chat or actor ID changes."""
        return self._scope_change(scope, expected, lambda current: current.named(title))

    def attach_world(self, scope: ScopeRef, source_id: str, *, expected: str) -> ChatChange:
        found = self.store.check_source(source_id)
        if isinstance(found, str):
            raise InvalidBinding(f"world {source_id} cannot be attached: {found}", found)
        world = WorldBinding(source_id, found.etag, self._clock())
        return self._scope_change(scope, expected, lambda current: current.with_world(world))

    def detach_world(self, scope: ScopeRef, *, expected: str) -> ChatChange:
        return self._scope_change(scope, expected, lambda current: current.with_world(None))

    def set_session_zero(self, scope: ScopeRef, value: Mapping[str, object] | None, *, expected: str) -> ChatChange:
        """Store 016's configuration verbatim. The caller (035) must authenticate who set it."""
        return self._scope_change(scope, expected, lambda current: current.with_session_zero(value))

    # -- helpers -----------------------------------------------------------------------------------

    def _id(self, prefix: str) -> str:
        value = self._new_id(prefix)
        validate_segment(value, "generated id")
        return value

    def _current(self, chat: ChatRef, expected: str) -> tuple[ChatRecord, ScopeRecord]:
        """The chat to edit and the stored envelope its rewrite keeps."""
        record, etag, stored = self._read_chat(chat)
        if etag != expected:
            raise StaleWrite([("/".join(address_parts(chat)), "changed")])
        return record, stored

    def _taken(self, chat: ChatRef, record: ChatRecord) -> set[str]:
        """Actor ids a new actor may not use: every instance file, and this chat's roster even if a file vanished."""
        taken = {found.instance_id for found in self.store.campaign_instances(chat.campaign_id)}
        return taken | {entry.actor.instance_id for entry in record.roster}

    def _resolve(self, ref: SourceRef, role: str) -> tuple[str, str]:
        if role not in ROLE_KINDS or ref.kind != ROLE_KINDS[role]:
            raise InvalidBinding(f"role {role!r} cannot bind {ref}", "role_kind")
        found = self.store.check_definition(ref)
        if isinstance(found, str):
            raise InvalidBinding(f"{ref} cannot be bound: {found}", found)
        return found.ref.revision, found.etag  # type: ignore[return-value]

    def _prepare(self, campaign: str, ref: SourceRef, role: str, reuse: InstanceRef | None,
                 taken: set[str]) -> tuple[NewBinding, RecordEnvelope | None]:
        revision, definition_etag = self._resolve(ref, role)
        if role not in ACTOR_ROLES:
            if reuse is not None:
                raise InvalidBinding(f"a {role} binding has no actor", "role_kind")
            return NewBinding(ref, role, revision), None
        if reuse is not None:
            self._check_reuse(campaign, reuse, ref)
            return NewBinding(ref, role, revision, reuse, CAMPAIGN_CONTINUITY), None
        base, number = ref.entity_id[:100], 1
        while f"{base}-{number}" in taken:
            number += 1
        actor = InstanceRef(campaign, "character", f"{base}-{number}")
        taken.add(actor.instance_id)
        envelope = RecordEnvelope(
            actor, instance_to_payload(EntityInstance(actor, source=ref)), owner=self._owner,
            derived_from=str(ref), base=str(ref.at(revision)), base_etag=definition_etag,
            extra={"schema": ENTITY_SCHEMA})
        return NewBinding(ref, role, revision, actor, NOT_WITNESSED), envelope

    def _check_reuse(self, campaign: str, actor: InstanceRef, ref: SourceRef) -> None:
        if actor.campaign_id != campaign or actor.kind != "character":
            raise InvalidBinding(f"{actor} is not a character of {campaign}", "actor_mismatch")
        try:
            stored = self.store.read(actor)
        except RecordNotFound:
            raise InvalidBinding(f"{actor} does not exist", "missing_actor") from None
        try:
            source = SourceRef.parse(stored.envelope.derived_from)  # type: ignore[union-attr]
        except ValueError:
            source = None
        if source is None or source.entity_key != ref.entity_key:
            raise InvalidBinding(f"{actor} is not an instance of {ref.unpinned()}", "actor_mismatch")

    def _chat_record(self, record: ChatRecord, base: ScopeRecord | None = None) -> ScopeRecord:
        return self._envelope(record.ref, CHAT_SCHEMA, chat_to_payload(record), base)

    def _scope_record(self, scope: PlayScope, base: ScopeRecord | None = None) -> ScopeRecord:
        return self._envelope(scope.ref, SCOPE_SCHEMA, scope_to_payload(scope), base)

    def _envelope(self, ref: ScopeRef | ChatRef, schema: str, payload: dict, base: ScopeRecord | None) -> ScopeRecord:
        """A rewrite keeps the stored envelope's unknown fields and its original attribution, even none."""
        if base is None:
            return ScopeRecord(ref, schema, payload, owner=self._owner)
        return ScopeRecord(ref, schema, payload, owner=base.owner, extra=base.extra)

    def _chat_change(self, chat: ChatRef, record: ChatRecord, base: ScopeRecord, expected: str,
                     envelope: RecordEnvelope | None = None) -> ChatChange:
        puts: list[tuple[RecordEnvelope | ScopeRecord, str | None]] = [(self._chat_record(record, base), expected)]
        actors: tuple[InstanceRef, ...] = ()
        if envelope is not None:
            puts.append((envelope, None))
            actors = (envelope.address,)  # type: ignore[assignment]
        return ChatChange(self, tuple(puts), chat.scope, chat, actors)

    def _scope_change(self, scope: ScopeRef, expected: str,
                      change: Callable[[PlayScope], PlayScope]) -> ChatChange:
        current, etag, stored = self._read_scope(scope)
        if etag != expected:
            raise StaleWrite([("/".join(address_parts(scope)), "changed")])
        return ChatChange(self, ((self._scope_record(change(current), stored), expected),), scope)


def _problem_records(view: LibraryView) -> tuple[set[ChatRef], set[ScopeRef]]:
    """Chat and scope files that exist but whose envelope the store could not read."""
    chats: set[ChatRef] = set()
    scopes: set[ScopeRef] = set()
    for problem in view.problems:
        found = classify(tuple(problem.path.split("/")))
        if found is not None and found[0] == "chat":
            chats.add(found[1])  # type: ignore[arg-type]
        elif found is not None and found[0] == "scope":
            scopes.add(found[1])  # type: ignore[arg-type]
    return chats, scopes


def _broken(view: LibraryView, record: ChatRecord, scope: PlayScope | None) -> list[BrokenBinding]:
    broken: list[BrokenBinding] = []

    def reason(ref: SourceRef) -> str | None:
        if ref.source in view.excluded:
            return "excluded_source"
        if ref.unpinned() in view.dropped:
            return "entity_dropped"
        found = view.index.check(ref)
        return None if found is None else found.reason

    if record.launch.ref is not None and (why := reason(record.launch.ref)) is not None:
        broken.append(BrokenBinding("launch", str(record.launch.ref), why))
    for binding in record.active_bindings():
        if (why := reason(binding.ref)) is not None:
            broken.append(BrokenBinding(f"bindings.{binding.role}", str(binding.ref), why))
    for entry in record.roster:
        if entry.actor not in view.instances:
            broken.append(BrokenBinding("roster.actor", str(entry.actor), "missing_actor"))
    if scope is not None and scope.world is not None:
        world = scope.world.source
        if world in view.excluded:
            broken.append(BrokenBinding("world", world, "excluded_source"))
        elif world not in view.sources:
            broken.append(BrokenBinding("world", world, "missing_source"))
        elif view.sources[world].etag != scope.world.observed_manifest_etag:
            broken.append(BrokenBinding("world", world, "world_drift"))
    return broken
