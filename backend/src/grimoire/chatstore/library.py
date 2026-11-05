"""Store-backed world-optional chats (Sprint 107): start, bind, name and find chats durably.

``ChatLibrary`` is trusted application code and is never handed to a worker. Every mutating
method returns a ``ChatChange``: one store transaction a human can ``preview`` and approve
before ``commit``. The store applies CAS, authorization, link/V1 guards, journal and recovery to
every record the change touches.

New bindings are validated against one source's own files with ``load()``'s rules
(``LibraryStore.check_definition``, which also consults the store's shared, cached whole-library
exclusions); ``load()`` is never called for a write. Opening a
chat and building navigation do load the library, and they never fail because a source is gone:
broken references are reported, never rebound.

No model call is made anywhere. Binding a source writes no event, belief or evidence, and a new
actor is ``not_witnessed``: it saw none of the chat's earlier history. A new actor's id is never one
``campaign_taken_ids`` reserves, so it inherits no other individual's past.
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
from grimoire.core.identity import ChatRef, InstanceRef, ScopeRef, SourceRef, paired_instances, validate_segment
from grimoire.effectstore import accepted_instance_ids
from grimoire.evidencestore import evidence_instance_ids
from grimoire.graphstore import relation_endpoint_ids
from grimoire.holdingstore import holdings_holder_ids, holdings_peer_ids
from grimoire.imagestore import placement_subject_ids
from grimoire.ledgers import sovereignty_actor_ids
from grimoire.library import LibraryPathError
from grimoire.store import (
    CampaignRecords, LibraryStore, LibraryView, RecordEnvelope, RecordNotFound, ScopeRecord, StaleWrite, StoreError,
    TakenIdsUnlisted,
)
from grimoire.store.layout import address_parts, classify

#: (role, entity key) -> an existing campaign actor to reuse instead of creating one
ReuseMap = Mapping[tuple[str, tuple[str, str, str]], InstanceRef]
_ALLOCATION_ATTEMPTS = 16


def campaign_taken_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    """Every instance id a new individual in this campaign must not take: the one reservation set.

    A hand-deleted individual's id must never go to a different one, who would silently inherit its
    past. Reserved: every instance file of any kind (a pair two kinds share keeps both); every id
    named by a relation (017), a chat roster (107), an accepted event (021), the events, evidence and
    knowledge log (020), a holder file's log (023's move peers) or the sovereignty ledger (010b's bare
    ``actor_id``s), each scanned raw so an undecodable record still protects its ids; every holder with
    a holdings file (023) and every subject with a campaign placement folder (018), from file names.
    Look-alike holder and placement names count as the ids they fold to.

    A file or folder that could name ids but cannot be read or listed raises ``TakenIdsUnlisted``
    (``HoldingsUnlisted`` is one case): allocation fails closed rather than guess. Every instance
    allocator (107 here; 024, 026, 037 later) must use this at plan time and again under the store
    lock at commit, and any later record family that names instance ids must extend it. Its cost
    grows with play history: it reads every relation, chat, accepted-event, knowledge and holder file
    and every sovereignty batch of the campaign.
    """
    taken = {found.instance_id for found in store.campaign_instances(campaign_id)}
    taken |= _named_ids(store.campaign_relations(campaign_id), campaign_id, "relation")
    taken |= relation_endpoint_ids(store, campaign_id)
    taken |= _named_ids(store.campaign_chats(campaign_id), campaign_id, "chat")
    taken |= accepted_instance_ids(store, campaign_id)
    taken |= holdings_holder_ids(store, campaign_id)
    taken |= holdings_peer_ids(store, campaign_id)
    taken |= evidence_instance_ids(store, campaign_id)
    taken |= placement_subject_ids(store, campaign_id)
    taken |= sovereignty_actor_ids(store, campaign_id)
    return frozenset(taken)


def _named_ids(found: CampaignRecords, campaign_id: str, family: str) -> set[str]:
    """Instance ids of this campaign anywhere in a family's raw payloads; fails closed on any problem."""
    if found.problems:
        paths = tuple(sorted({problem.path for problem in found.problems}))
        raise TakenIdsUnlisted(f"{family} files of {campaign_id} cannot all be read ({', '.join(paths)}), so the "
                               "instance ids they name cannot be reserved", paths)
    taken: set[str] = set()
    stack: list[object] = [stored.record.payload for stored in found.records.values()]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            try:
                ref = InstanceRef.parse(value)
            except ValueError:
                continue
            if ref.campaign_id == campaign_id:
                taken.add(ref.instance_id)
        elif isinstance(value, Mapping):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    return taken


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
    reused_actors: tuple[InstanceRef, ...] = ()  # existing actors this change binds; re-checked for a pair
    #: each reused actor's instance-file etag as checked; a file replaced since may hold a different individual
    reused_etags: Mapping[InstanceRef, str] = field(default_factory=dict)
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
            if self.new_scope:  # nothing may have started this campaign (or its ledger) since the plan
                if store.campaign_presence(campaign) != "absent":
                    raise StaleWrite([(f"campaigns/{campaign}", "exists")])
                if store.campaign_authority_presence(campaign) != "absent":
                    raise StaleWrite([(f"authority/campaigns/{campaign}", "exists")])
            if self.new_actors:
                taken = campaign_taken_ids(store, campaign)
                clash = [actor for actor in self.new_actors if actor.instance_id in taken]
                if clash:
                    raise StaleWrite([("/".join(address_parts(actor)), "id taken") for actor in clash])
            replaced = []
            for actor, etag in self.reused_etags.items():  # the very file checked at plan time, same bytes
                try:
                    current: str | None = store.read(actor).etag
                except RecordNotFound:
                    current = None
                except (StoreError, LibraryPathError):
                    current = ""  # there, but unreadable now: not the file that was checked
                if current != etag:
                    replaced.append(("/".join(address_parts(actor)), "absent" if current is None else "changed"))
            if replaced:
                raise StaleWrite(replaced)
            if self.reused_actors:  # another kind may have taken a reused actor's id since the plan
                paired = paired_instances(store.campaign_instances(campaign))
                clash = [actor for actor in self.reused_actors if actor in paired]
                if clash:
                    raise StaleWrite([("/".join(address_parts(actor)), "instance_id_conflict") for actor in clash])
            result = self._transaction().commit(self.library.handle, approvals)
            self._committed = True
        return ChatCommit(result.etags, self.chat, self.scope, self.new_actors)


class _LazyTaken:
    """Ids a new actor may not use, computed only when an actor id is actually allocated.

    Binding a scenario or lorebook, or reusing an existing actor, never lists the campaign, so a
    damaged history folder (023's ``HoldingsUnlisted``) only blocks the operations that allocate.
    """

    def __init__(self, compute: Callable[[], set[str]]) -> None:
        self._compute = compute
        self._ids: set[str] | None = None

    def _get(self) -> set[str]:
        if self._ids is None:
            self._ids = set(self._compute())
        return self._ids

    def __contains__(self, value: object) -> bool:
        return value in self._get()

    def add(self, value: str) -> None:
        self._get().add(value)


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
        with self.store.read_pass():  # every binding is validated against one verified exclusion set
            return self._start_chat(launch, attach, persona, label, scope, reuse_actors)

    def _start_chat(self, launch: Launch, attach: Iterable[tuple[SourceRef, str]], persona: SourceRef | None,
                    label: str | None, scope: ScopeRef | None, reuse_actors: ReuseMap | None) -> ChatChange:
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
            scope, new_scope = self._allocate_scope(), True
            scope_puts.append((self._scope_record(PlayScope(scope, at)), None))
            fresh = scope.campaign_id  # nothing should name it yet; the commit re-checks with the same set
            taken: set[str] | _LazyTaken = _LazyTaken(lambda: campaign_taken_ids(self.store, fresh))
        else:
            new_scope = False
            if self.store.campaign_presence(scope.campaign_id) != "present":
                raise InvalidBinding(f"{scope} does not exist", "missing_scope")
            try:
                self.store.read(scope)
            except RecordNotFound:  # a campaign from before 107 (or 026): give it a scope record now
                scope_puts.append((self._scope_record(PlayScope(scope, at)), None))
            campaign = scope.campaign_id
            taken = _LazyTaken(lambda: campaign_taken_ids(self.store, campaign))
        chat = ChatRef(scope.campaign_id, self._id("t"))
        bindings, actors, reused = [], [], {}
        for ref, role in wanted:
            binding, envelope, etag = self._prepare(scope.campaign_id, ref, role,
                                                    reuse.pop((role, ref.entity_key), None), taken)
            bindings.append(binding)
            if envelope is not None:
                actors.append(binding.actor)
                actor_puts.append((envelope, None))
            elif binding.actor is not None:
                assert etag is not None  # _check_reuse returned it
                reused[binding.actor] = etag
        if reuse:
            raise InvalidBinding(f"actors to reuse match no binding: {sorted(map(str, reuse.values()))}",
                                 "reuse_unmatched")
        record = ChatRecord.start(chat, launch, at, tuple(bindings), label)
        puts = (*scope_puts, (self._chat_record(record), None), *actor_puts)
        return ChatChange(self, puts, scope, chat, tuple(actors), new_scope, tuple(reused), reused)

    def _allocate_scope(self) -> ScopeRef:
        """A campaign id with no ``campaigns/<id>`` folder and no sovereignty ledger: a ledger outlives a
        hand-deleted campaign folder, and a new campaign given its id would inherit its controls."""
        for _ in range(_ALLOCATION_ATTEMPTS):
            campaign = self._id("q")
            if (self.store.campaign_presence(campaign) == "absent"  # a look-alike folder counts as taken
                    and self.store.campaign_authority_presence(campaign) == "absent"):
                return ScopeRef(campaign)
        raise StoreError("could not allocate an unused campaign id")

    # -- chat edits -----------------------------------------------------------------------------

    def attach_source(self, chat: ChatRef, ref: SourceRef, role: str, *, expected: str,
                      actor: InstanceRef | None = None) -> ChatChange:
        record, base = self._current(chat, expected)
        taken = _LazyTaken(lambda: self._taken(chat, record))
        binding, envelope, etag = self._prepare(chat.campaign_id, ref, role, actor, taken)
        return self._chat_change(chat, record.attach(binding, self._clock()), base, expected, envelope, binding.actor,
                                 etag)

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
        envelope = bound = etag = None
        if ref is not None:
            detached = record
            binding, envelope, etag = self._prepare(chat.campaign_id, ref, "persona", actor,
                                                    _LazyTaken(lambda: self._taken(chat, detached)))
            record, bound = record.attach(binding, at), binding.actor
        return self._chat_change(chat, record, base, expected, envelope, bound, etag)

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
        """Actor ids a new actor may not use: ``campaign_taken_ids``, and this chat's roster as read."""
        return set(campaign_taken_ids(self.store, chat.campaign_id)) | {entry.actor.instance_id
                                                                        for entry in record.roster}

    def _resolve(self, ref: SourceRef, role: str) -> tuple[str, str]:
        if role not in ROLE_KINDS or ref.kind != ROLE_KINDS[role]:
            raise InvalidBinding(f"role {role!r} cannot bind {ref}", "role_kind")
        found = self.store.check_definition(ref)
        if isinstance(found, str):
            raise InvalidBinding(f"{ref} cannot be bound: {found}", found)
        return found.ref.revision, found.etag  # type: ignore[return-value]

    def _prepare(self, campaign: str, ref: SourceRef, role: str, reuse: InstanceRef | None,
                 taken: set[str] | _LazyTaken) -> tuple[NewBinding, RecordEnvelope | None, str | None]:
        """The binding, a new actor's envelope (or ``None``) and a reused actor's etag as checked (or ``None``)."""
        revision, definition_etag = self._resolve(ref, role)
        if role not in ACTOR_ROLES:
            if reuse is not None:
                raise InvalidBinding(f"a {role} binding has no actor", "role_kind")
            return NewBinding(ref, role, revision), None, None
        if reuse is not None:
            etag = self._check_reuse(campaign, reuse, ref)
            return NewBinding(ref, role, revision, reuse, CAMPAIGN_CONTINUITY), None, etag
        base, number = ref.entity_id[:100], 1
        while f"{base}-{number}" in taken:
            number += 1
        actor = InstanceRef(campaign, "character", f"{base}-{number}")
        taken.add(actor.instance_id)
        envelope = RecordEnvelope(
            actor, instance_to_payload(EntityInstance(actor, source=ref)), owner=self._owner,
            derived_from=str(ref), base=str(ref.at(revision)), base_etag=definition_etag,
            extra={"schema": ENTITY_SCHEMA})
        return NewBinding(ref, role, revision, actor, NOT_WITNESSED), envelope, None

    def _check_reuse(self, campaign: str, actor: InstanceRef, ref: SourceRef) -> str:
        """The etag of the actor's file as checked: the commit binds only these very bytes."""
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
        if actor in paired_instances(self.store.campaign_instances(campaign)):  # load() leaves both members out
            raise InvalidBinding(f"{actor}'s id is also used by another kind in {campaign}", "instance_id_conflict")
        return stored.etag

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
                     envelope: RecordEnvelope | None = None, bound: InstanceRef | None = None,
                     bound_etag: str | None = None) -> ChatChange:
        """``bound`` is the actor the change binds: new if ``envelope`` creates it, otherwise reused, its
        file then pinned at ``bound_etag`` (the etag ``_check_reuse`` read)."""
        puts: list[tuple[RecordEnvelope | ScopeRecord, str | None]] = [(self._chat_record(record, base), expected)]
        actors: tuple[InstanceRef, ...] = ()
        reused: tuple[InstanceRef, ...] = ()
        etags: dict[InstanceRef, str] = {}
        if envelope is not None:
            puts.append((envelope, None))
            actors = (envelope.address,)  # type: ignore[assignment]
        elif bound is not None:
            reused = (bound,)
            if bound_etag is not None:
                etags[bound] = bound_etag
        return ChatChange(self, tuple(puts), chat.scope, chat, actors, reused_actors=reused, reused_etags=etags)

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
