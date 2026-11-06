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

import json
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from hashlib import sha256

from grimoire.adjudicationstore import adjudication_actor_ids
from grimoire.cards import scenario_lorebook
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
from grimoire.scenestore import turn_instance_ids
from grimoire.graphstore import relation_endpoint_ids
from grimoire.holdingstore import holdings_holder_ids, holdings_peer_ids
from grimoire.imagestore import ART_PIN, freeze_art, pinned_base, placement_subject_ids
from grimoire.ledgers import security_campaign_ids, security_instance_ids, sovereignty_actor_ids
from grimoire.library import LibraryPathError
from grimoire.decisionstore import decision_actor_ids
from grimoire.obstaclestore import obstacle_actor_ids
from grimoire.possessions import POSSESSION_KEY, party_ids
from grimoire.security import payload_digest
from grimoire.store import (
    CampaignRecords, LibraryStore, LibraryView, LinkRefused, MalformedRecord, RecordEnvelope, RecordNotFound, ScopeRecord,
    StaleWrite, StoreError, TakenIdsUnlisted, UnstorableIdentifier,
)
from grimoire.store.jsonfmt import thaw
from grimoire.store.layout import address_parts, classify
from grimoire.sync.documents import DEVICE_FILE, parse_device

#: (role, entity key) -> an existing campaign actor to reuse instead of creating one
ReuseMap = Mapping[tuple[str, tuple[str, str, str]], InstanceRef]
_ALLOCATION_ATTEMPTS = 16


class DeviceIdentityUnusable(Exception):
    """This library has a ``.sync/device.json`` that cannot be read or is damaged: no new IDs."""


def device_tag(store: LibraryStore) -> str:
    """``-`` and eight hex digits naming this library's device, or ``""`` for a library with none.

    028: the first free number is only free on *this* device. Two paired devices that each create
    the same thing offline (a chat actor for one character, a unique item from one template) would
    both draw ``mara-1`` or ``longsword-1``, and 019 would then merge two different individuals
    (their knowledge, holdings, custody and evidence) into one file, or report a conflict on what
    are really two. Qualifying the number with a short digest of the device ID (``mara-3f2a91c0-1``)
    keeps the two apart without any coordination between the devices, which is the whole point of
    offline work. A library that was never given a device identity has never synced, so it keeps the
    original numbering (``mara-1``) and every existing library and test fixture reads exactly as
    before. The digest, not the ID itself, goes in the instance ID: device IDs are free-form within
    the segment rules and could be long, and an instance ID should not carry a label the user chose
    for a machine. Only new IDs change: an existing instance keeps the ID it was given.

    A damaged or unreadable identity file (a link or folder included) raises ``DeviceIdentityUnusable``
    rather than falling back to plain numbers, so a broken identity never quietly brings back the
    numbering that collides. A library whose ``.sync`` folder was deleted outright reads as never
    identified, until ``identify`` is run again.
    """
    try:
        data = store.read_sync_state(DEVICE_FILE)
    except (StoreError, LibraryPathError, OSError) as error:  # a link or folder too: never "no identity"
        raise DeviceIdentityUnusable(f"cannot read this library's device identity: {error}") from error
    if data is None:
        return ""
    device = parse_device(data)
    if device is None:
        raise DeviceIdentityUnusable("this library's device identity is damaged; repair .sync/device.json")
    return "-" + sha256(device.encode()).hexdigest()[:8]


def campaign_taken_ids(store: LibraryStore, campaign_id: str, *,
                       spent: Collection[tuple[str, str, str]] = ()) -> frozenset[str]:
    """Every instance id a new individual in this campaign must not take: the one reservation set.

    A hand-deleted individual's id must never go to a different one, who would silently inherit its
    past. Reserved: every instance file of any kind (a pair two kinds share keeps both); every id
    named by a relation (017), a chat roster (107), a chat's transcript pages (036), an accepted event (021), the events, evidence and
    knowledge log (020), a holder file's log (023's move peers), the sovereignty ledger (010b's bare
    ``actor_id``s), the adjudication ledger (029's frames, rulings and decisions) or a security-ledger grant or approval scoped to one individual (its instance,
    holder or placement address), each scanned raw so an undecodable record still protects its ids;
    every owner, holder and wearer a unique item's possession record names (034, scanned raw);
    every holder with a holdings file (023) and every subject with a campaign placement folder (018),
    from file names.
    Look-alike holder and placement names count as the ids they fold to.

    A file or folder that could name ids but cannot be read or listed raises ``TakenIdsUnlisted``
    (``HoldingsUnlisted`` is one case): allocation fails closed rather than guess. An instance file the
    store cannot read as a record is the exception: every ``<kind>/<id>`` in its bytes is reserved, and a link or
    folder at an instance path takes only its own id (the store never reads it as a record). Every instance
    allocator (107 here; 024, 026, 037 later) must use this at plan time and again under the store
    lock at commit, and any later record family that names instance ids must extend it. Its cost
    grows with play history: it reads every instance, relation, chat, transcript page, accepted-event, knowledge,
    holder and turn file and every sovereignty, adjudication and obstacle batch of the campaign, and every security
    batch of the library.

    ``spent`` is ``(approval id, address, payload digest)`` of the approvals a commit supplies for
    its own writes: at that commit's re-check they name its new individuals by design and do not
    count against them (as ``ChatChange.commit`` does).
    """
    return _taken_ids(store, campaign_id, security=True, spent=spent)


def _taken_ids(store: LibraryStore, campaign_id: str, *, security: bool,
               spent: Collection[tuple[str, str, str]] = ()) -> frozenset[str]:
    taken = {found.instance_id for found in store.campaign_instances(campaign_id)}
    taken |= _named_ids(store.campaign_relations(campaign_id), campaign_id, "relation")
    taken |= relation_endpoint_ids(store, campaign_id)
    taken |= _named_ids(store.campaign_chats(campaign_id), campaign_id, "chat")
    taken |= _named_ids(store.campaign_transcripts(campaign_id), campaign_id, "transcript")
    taken |= accepted_instance_ids(store, campaign_id)
    taken |= holdings_holder_ids(store, campaign_id)
    taken |= holdings_peer_ids(store, campaign_id)
    taken |= evidence_instance_ids(store, campaign_id)
    taken |= placement_subject_ids(store, campaign_id)
    taken |= sovereignty_actor_ids(store, campaign_id)
    taken |= possession_party_ids(store, campaign_id)
    taken |= adjudication_actor_ids(store, campaign_id)
    taken |= turn_instance_ids(store, campaign_id)
    taken |= obstacle_actor_ids(store, campaign_id)
    taken |= decision_actor_ids(store, campaign_id)
    if security:  # skipped only by _fresh_campaign_taken_ids
        taken |= security_instance_ids(store, campaign_id, spent=spent)
    return frozenset(taken)


def _fresh_campaign_taken_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    """``campaign_taken_ids`` for a campaign id just found outside ``security_campaign_ids`` under the
    same store lock: no grant or approval names ``campaigns/<id>`` or anything under it (at commit,
    none but the approvals of that commit's own writes), so none can name another of its individuals
    and the security ledger (a whole-library audit that can be large) is not read a second time. Only
    a new scope's allocation and its commit re-check may use this."""
    return _taken_ids(store, campaign_id, security=False)


def possession_party_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    """Instance ids named by the campaign's unique items (034): owners, holders and wearers, current and
    logged, in each instance file's ``possession`` record, scanned raw. An instance file the store cannot
    read as a record (malformed, a newer format) could be an item, so every ``<kind>/<id>`` in its exact
    bytes is reserved. A link or a folder at an instance path is never read as a record, so it holds no
    item the library can act on: only its own id is taken (from the listing), and the rest of the
    campaign stays allocatable. One whose bytes cannot be read for any other reason (an I/O error)
    raises ``TakenIdsUnlisted``. Reads every instance file of the campaign."""
    taken: set[str] = set()
    unread: list[str] = []
    for ref in store.campaign_instances(campaign_id):
        path = "/".join(address_parts(ref))
        try:
            stored = store.read(ref)
        except (RecordNotFound, LinkRefused):
            continue
        except MalformedRecord:
            try:
                data = store.read_exact(path)
            except (LinkRefused, UnstorableIdentifier):
                continue  # a link or a folder: never an item's record
            except (StoreError, LibraryPathError, ValueError):
                data = None
                unread.append(path)
            if data is not None:
                taken |= _party_ids_in_bytes(data)
            continue
        except (StoreError, LibraryPathError):
            unread.append(path)
            continue
        payload = stored.envelope.payload  # type: ignore[union-attr]
        if isinstance(payload, Mapping) and POSSESSION_KEY in payload:
            taken |= party_ids(payload[POSSESSION_KEY])
    if unread:
        raise TakenIdsUnlisted(f"instance files of {campaign_id} cannot all be read ({', '.join(unread)}), so the "
                               "instance ids a unique item names cannot be reserved", tuple(unread))
    return frozenset(taken)


_PARTY_TEXT = re.compile(r"(?<![a-z0-9._-])([a-z0-9][a-z0-9._-]*)/([a-z0-9][a-z0-9._-]*)")


def _party_ids_in_bytes(data: bytes) -> frozenset[str]:
    """Every instance id an instance file the store cannot read as a record might name as a party.

    Every ``<kind>/<instance_id>`` in its text counts (an escaped ``\\/`` too), and, when the bytes parse
    as JSON, every one inside each decoded string (a ``\\u`` escape too). Over-reserving only skips an id.
    """
    text = data.decode("utf-8", "replace")
    strings = [text.replace("\\/", "/")]
    try:
        document = json.loads(text)
    except (ValueError, RecursionError, MemoryError):  # not JSON, or nested past the parser: the text scan stands
        document = None
    stack: list[object] = [document]
    while stack:  # every decoded string too, so an escaped slash anywhere in free text still counts
        value = stack.pop()
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, Mapping):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return frozenset(instance_id for string in strings for kind, instance_id in _PARTY_TEXT.findall(string)
                     if _is_party(kind, instance_id))


def _is_party(kind: str, instance_id: str) -> bool:
    try:
        InstanceRef("c", kind, instance_id)
    except ValueError:
        return False
    return True


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
    #: the campaign's chats at the etags read when a reused actor's played revision was taken from them
    #: (``campaign_plays``), or ``None``: a chat added, removed or rebound since could pin it elsewhere
    played_from: Mapping[ChatRef, str] | None = None
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
            # the approvals this commit supplies for its own writes name the new campaign and actors
            # by design (preview, register approvals, commit), so they do not count against them
            spent = self._spent(approvals) if approvals and (self.new_scope or self.new_actors) else ()
            if self.new_scope:  # nothing may have started this campaign (or its ledger) since the plan
                if store.campaign_presence(campaign) != "absent":
                    raise StaleWrite([(f"campaigns/{campaign}", "exists")])
                if store.campaign_authority_presence(campaign) != "absent":
                    raise StaleWrite([(f"authority/campaigns/{campaign}", "exists")])
                if campaign in security_campaign_ids(store, spent=spent):
                    raise StaleWrite([("authority/security", f"a grant or approval names campaigns/{campaign}")])
            if self.new_actors:  # a new scope's id was just proven ungranted above, under this lock
                taken = _taken_ids(store, campaign, security=not self.new_scope, spent=spent)
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
            if self.played_from is not None:  # the chats the played revision rests on, as read (as 042 re-checks)
                found = store.campaign_chats(campaign)
                replaced += [(problem.path, "changed") for problem in found.problems]
                current_chats = {ref: stored.etag for ref, stored in found.records.items()}
                for ref in sorted(set(self.played_from) | set(current_chats), key=str):
                    if self.played_from.get(ref) != current_chats.get(ref):
                        replaced.append(("/".join(address_parts(ref)),
                                         "absent" if ref not in current_chats else "changed"))
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

    def _spent(self, approvals: Mapping[str, str]) -> frozenset[tuple[str, str, str]]:
        """``(approval id, address, digest)`` of each supplied approval, for the bytes this change writes."""
        preview = self.preview()
        return frozenset((approval, address, payload_digest(preview[address]))
                         for address, approval in approvals.items()
                         if type(address) is str and type(approval) is str and address in preview)


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


def _check_role(ref: SourceRef, role: str) -> None:
    if role not in ROLE_KINDS or ref.kind != ROLE_KINDS[role]:
        raise InvalidBinding(f"role {role!r} cannot bind {ref}", "role_kind")


def campaign_plays(store: LibraryStore, actor: InstanceRef, ref: SourceRef,
                   reads: list[dict[ChatRef, str]] | None = None) -> frozenset[SourceRef] | None:
    """The revisions of ``ref``'s entity that the active pinned bindings of ``actor`` in its campaign's
    chats name (empty: no chat binds it pinned), or ``None`` when a chat of the campaign cannot be read
    or interpreted, so its bindings cannot all be known. 042's rebase takes the one agreed revision as
    the common ancestor and refuses disagreement; a new pinned binding of the actor keeps to that rule.

    When the answer rests on the chats (it is not ``None``), the etag of every chat of the campaign as read
    is appended to ``reads``, so a commit can re-check that none was added, removed or rebound since."""
    found = store.campaign_chats(actor.campaign_id)
    if found.problems:
        return None
    revisions: set[SourceRef] = set()
    for chat, stored in found.records.items():
        try:
            record = chat_from_payload(chat, stored.record.schema, stored.record.payload)  # type: ignore[arg-type]
        except ChatCodecError:
            return None
        revisions.update(binding.ref for binding in record.active_bindings()
                         if binding.actor == actor and binding.ref.pinned
                         and binding.ref.entity_key == ref.entity_key)
    if reads is not None:
        reads.append({chat: stored.etag for chat, stored in found.records.items()})
    return frozenset(revisions)


def instance_plays(envelope: RecordEnvelope, ref: SourceRef) -> SourceRef | None:
    """The revision of ``ref``'s entity an actor's own file says it plays: its ``source`` if pinned (``None``
    if it follows the head, so it plays the head now), else, with no ``source``, its pinned ``base``."""
    payload = envelope.payload if isinstance(envelope.payload, Mapping) else {}
    source = payload.get("source")
    text = source if source is not None else envelope.base
    try:
        played = SourceRef.parse(text) if isinstance(text, str) else None
    except ValueError:
        return None
    if played is not None and played.pinned and played.entity_key == ref.entity_key:
        return played
    return None


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
                   reuse_actors: ReuseMap | None = None, follow: bool = False) -> ChatChange:
        """A new chat, by default in a new world-less scope; ``scope`` continues an existing campaign.

        Every source is **pinned** to the revision it resolves to now (the launch included), so a later
        revision reaches the chat only through 042's reviewed rebase. ``follow=True`` is the explicit
        opt-in to bind unpinned refs as given, following their heads."""
        with self.store.read_pass():  # every binding is validated against one verified exclusion set
            return self._start_chat(launch, attach, persona, label, scope, reuse_actors, follow)

    def _start_chat(self, launch: Launch, attach: Iterable[tuple[SourceRef, str]], persona: SourceRef | None,
                    label: str | None, scope: ScopeRef | None, reuse_actors: ReuseMap | None,
                    follow: bool) -> ChatChange:
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
            taken: set[str] | _LazyTaken = _LazyTaken(lambda: _fresh_campaign_taken_ids(self.store, fresh))
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
        reads: list[dict[ChatRef, str]] = []
        for ref, role in wanted:
            binding, envelope, etag = self._prepare(scope.campaign_id, ref, role,
                                                    reuse.pop((role, ref.entity_key), None), taken, follow, reads)
            bindings.append(binding)
            if envelope is not None and etag is None:
                actors.append(binding.actor)
                actor_puts.append((envelope, None))
            elif binding.actor is not None:
                assert etag is not None  # _check_reuse returned it
                reused[binding.actor] = etag
                if envelope is not None:  # a reused actor a pinned chat pins (046)
                    actor_puts.append((envelope, etag))
        if reuse:
            raise InvalidBinding(f"actors to reuse match no binding: {sorted(map(str, reuse.values()))}",
                                 "reuse_unmatched")
        if launch.ref is not None:  # the launch names what its binding pins: the revision the chat began with
            launch = Launch(launch.kind, bindings[0].ref)
        record = ChatRecord.start(chat, launch, at, tuple(bindings), label)
        puts = (*scope_puts, (self._chat_record(record), None), *actor_puts)
        return ChatChange(self, puts, scope, chat, tuple(actors), new_scope, tuple(reused), reused,
                          played_from=_merged(reads))

    def _allocate_scope(self) -> ScopeRef:
        """A campaign id with no ``campaigns/<id>`` folder, no sovereignty ledger and no security-ledger
        grant or approval naming it. Both ledgers outlive a hand-deleted campaign folder: a new campaign
        given its id would inherit its controls, or hand an old grant's worker the new campaign.

        The security ledger is read once, and only once a drawn id is otherwise free, so a start that
        never gets that far pays nothing for it; an unreadable one raises ``TakenIdsUnlisted``."""
        granted: frozenset[str] | None = None
        for _ in range(_ALLOCATION_ATTEMPTS):
            campaign = self._id("q")
            if (self.store.campaign_presence(campaign) == "absent"  # a look-alike folder counts as taken
                    and self.store.campaign_authority_presence(campaign) == "absent"):
                if granted is None:
                    granted = security_campaign_ids(self.store)
                if campaign not in granted:
                    return ScopeRef(campaign)
        raise StoreError("could not allocate an unused campaign id")

    # -- chat edits -----------------------------------------------------------------------------

    def attach_source(self, chat: ChatRef, ref: SourceRef, role: str, *, expected: str,
                      actor: InstanceRef | None = None, follow: bool = False) -> ChatChange:
        """Bind ``ref``, pinned to the revision it resolves to now unless ``follow=True`` (see ``start_chat``)."""
        record, base = self._current(chat, expected)
        taken = _LazyTaken(lambda: self._taken(chat, record))
        reads: list[dict[ChatRef, str]] = []
        binding, envelope, etag = self._prepare(chat.campaign_id, ref, role, actor, taken, follow, reads)
        return self._chat_change(chat, record.attach(binding, self._clock()), base, expected, envelope, binding.actor,
                                 etag, _merged(reads))

    def detach_source(self, chat: ChatRef, ref: SourceRef, role: str, *, expected: str) -> ChatChange:
        """Tombstone a binding; works for a broken reference (the repair path). Actors stay.

        A lorebook a bound scenario implies (037; its snapshot selects it, unbound) is detached by
        recording it as bound and detached at once: the tombstone is the chat's opt-out, which
        ``chat_snapshot`` honours through scenario updates until the lorebook is attached again."""
        record, base = self._current(chat, expected)
        at = self._clock()
        if role == "lorebook" and not any(b.key == (role, ref.entity_key) for b in record.active_bindings(role)):
            implied = self._implied_lorebook(record, ref)
            if implied is not None:
                record = record.attach(NewBinding(implied, role, implied.revision), at)  # type: ignore[arg-type]
        return self._chat_change(chat, record.detach(ref, role, at), base, expected)

    def set_persona(self, chat: ChatRef, ref: SourceRef | None, *, expected: str,
                    actor: InstanceRef | None = None, follow: bool = False) -> ChatChange:
        """Replace the persona; the new one is pinned unless ``follow=True`` (see ``start_chat``)."""
        record, base = self._current(chat, expected)
        at = self._clock()
        current = record.persona
        if current is not None:
            if (ref is not None and actor is None and ref.entity_key == current.ref.entity_key
                    and current.actor in self.store.campaign_instances(chat.campaign_id)):
                actor = current.actor  # the same persona (perhaps another revision) keeps its body
                if (not follow and not ref.pinned and current.ref.pinned
                        and not isinstance(self.store.check_definition(current.ref), str)):
                    ref = current.ref  # and, named without a revision, the revision this chat plays it at
                    # (unless that revision no longer resolves: then, as detach and attach repair, the head)
            record = record.detach(current.ref, "persona", at)
        envelope = bound = etag = None
        reads: list[dict[ChatRef, str]] = []
        if ref is not None:
            detached = record
            binding, envelope, etag = self._prepare(chat.campaign_id, ref, "persona", actor,
                                                    _LazyTaken(lambda: self._taken(chat, detached)), follow, reads)
            record, bound = record.attach(binding, at), binding.actor
        return self._chat_change(chat, record, base, expected, envelope, bound, etag, _merged(reads))

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
        _check_role(ref, role)
        found = self.store.check_definition(ref)
        if isinstance(found, str):
            raise InvalidBinding(f"{ref} cannot be bound: {found}", found)
        return found.ref.revision, found.etag  # type: ignore[return-value]

    def _implied_lorebook(self, record: ChatRecord, ref: SourceRef) -> SourceRef | None:
        """The lorebook of ``ref``'s entity that one of the chat's bound scenarios names, as it names it
        (``grimoire.effective`` selects it with the scenario), or ``None``."""
        for binding in record.active_bindings("scenario"):
            found = self.store.check_definition(binding.ref)
            if isinstance(found, str):
                continue
            try:
                own = scenario_lorebook(found.envelope.extra.get("schema"), found.envelope.payload)
            except ValueError:
                continue
            if own is not None and own.entity_key == ref.entity_key:
                return own
        return None

    def _prepare(self, campaign: str, ref: SourceRef, role: str, reuse: InstanceRef | None,
                 taken: set[str] | _LazyTaken, follow: bool = False,
                 reads: list[dict[ChatRef, str]] | None = None
                 ) -> tuple[NewBinding, RecordEnvelope | None, str | None]:
        """The binding, a new actor's envelope (or a reused actor's rewrite with its pinned art, 046; or
        ``None``) and a reused actor's etag as checked (or ``None``).

        An unpinned ``ref`` is pinned to the revision it resolves to now, the very revision whose bytes
        were checked (and a new actor is based on), unless the caller asked to ``follow`` the head. For a
        reused actor it is pinned to the revision that actor plays (``_plays``), never to a newer head
        nobody reviewed for it; when it plays the head, or the revision it plays no longer resolves (the
        broken reference that detach and attach repair), to the revision it resolves to now."""
        _check_role(ref, role)
        if role not in ACTOR_ROLES and reuse is not None:
            raise InvalidBinding(f"a {role} binding has no actor", "role_kind")
        if reuse is not None:
            etag, stored = self._check_reuse(campaign, reuse, ref)
            if not follow and not ref.pinned:
                plays = self._plays(reuse, ref, stored, reads)
                if plays is not None and not isinstance(self.store.check_definition(plays), str):
                    ref = plays
            revision, _ = self._resolve(ref, role)
            if not follow and not ref.pinned:
                ref = ref.at(revision)
            rewrite = self._pin_reused(stored) if ref.pinned else None
            return NewBinding(ref, role, revision, reuse, CAMPAIGN_CONTINUITY), rewrite, etag
        revision, definition_etag = self._resolve(ref, role)
        if not follow and not ref.pinned:
            ref = ref.at(revision)
        if role not in ACTOR_ROLES:
            return NewBinding(ref, role, revision), None, None
        base, number = ref.entity_id[:100] + self._device_tag(), 1
        while f"{base}-{number}" in taken:
            number += 1
        actor = InstanceRef(campaign, "character", f"{base}-{number}")
        taken.add(actor.instance_id)
        extra: dict[str, object] = {"schema": ENTITY_SCHEMA}
        if ref.pinned:  # 046: a pinned actor keeps the pictures its revision shows now, until a reviewed rebase
            pin = freeze_art(self.store, ref.at(revision))
            if pin is not None:
                extra[ART_PIN] = pin
        envelope = RecordEnvelope(
            actor, instance_to_payload(EntityInstance(actor, source=ref)), owner=self._owner,
            derived_from=str(ref), base=str(ref.at(revision)), base_etag=definition_etag, extra=extra)
        return NewBinding(ref, role, revision, actor, NOT_WITNESSED), envelope, None

    def _plays(self, actor: InstanceRef, ref: SourceRef, stored: RecordEnvelope,
               reads: list[dict[ChatRef, str]] | None = None) -> SourceRef | None:
        """The revision of ``ref``'s entity the existing ``actor`` plays, by 042's ancestor rule: the one
        revision its active pinned chat bindings in the campaign agree on (composition follows the
        binding, so an explicit rebind counts); otherwise (no chat binds it pinned, the bindings disagree,
        or a chat cannot be read) what its own file says: its pinned ``source``, else, with no source, its
        pinned ``base``. Never a revision nobody reviewed for it: a damaged chat or a disagreement leaves
        the new binding at the instance's revision, and 042's review still refuses until it is resolved.
        ``None`` (it plays the head now) when the actor's ``source`` follows the head. ``stored`` is the
        actor's file as ``_check_reuse`` read it, the bytes the commit re-checks; the chats it read go into
        ``reads``, which the commit re-checks too."""
        bound = campaign_plays(self.store, actor, ref, reads)
        if bound is not None and len(bound) == 1:
            return next(iter(bound))
        return instance_plays(stored, ref)

    def _pin_reused(self, envelope: RecordEnvelope) -> RecordEnvelope | None:
        """A reused actor bound by a pinned chat, rewritten with the art it shows now when it has no
        pin for its own ``base`` (a following chat made it, or a build before 046 did), so the pinned
        chat's pictures hold still too (046). ``None``: it is already pinned, or cannot be. The put is
        guarded by the etag of the very file ``_check_reuse`` read as ``envelope``."""
        if envelope.base is None:
            return None
        if pinned_base(envelope.extra.get(ART_PIN)) == envelope.base:
            return None
        try:
            base = SourceRef.parse(envelope.base)
        except ValueError:
            return None
        pin = freeze_art(self.store, base) if base.pinned else None
        if pin is None:
            return None
        return replace(envelope, extra={**thaw(envelope.extra), ART_PIN: pin})

    def _device_tag(self) -> str:
        """This library's ``device_tag``; an unusable identity refuses the binding (``device_identity``)."""
        try:
            return device_tag(self.store)
        except DeviceIdentityUnusable as error:
            raise InvalidBinding(str(error), "device_identity") from error

    def _check_reuse(self, campaign: str, actor: InstanceRef, ref: SourceRef) -> tuple[str, RecordEnvelope]:
        """The etag of the actor's file as checked, and the file: the commit binds only these very bytes."""
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
        return stored.etag, stored.envelope  # type: ignore[return-value]

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
                     bound_etag: str | None = None, played_from: Mapping[ChatRef, str] | None = None) -> ChatChange:
        """``bound`` is the actor the change binds: new if ``envelope`` creates it, otherwise reused, its
        file then pinned at ``bound_etag`` (the etag ``_check_reuse`` read)."""
        puts: list[tuple[RecordEnvelope | ScopeRecord, str | None]] = [(self._chat_record(record, base), expected)]
        actors: tuple[InstanceRef, ...] = ()
        reused: tuple[InstanceRef, ...] = ()
        etags: dict[InstanceRef, str] = {}
        if envelope is not None and bound_etag is None:
            puts.append((envelope, None))
            actors = (envelope.address,)  # type: ignore[assignment]
        elif bound is not None:
            reused = (bound,)
            if bound_etag is not None:
                etags[bound] = bound_etag
                if envelope is not None:  # a reused actor a pinned chat pins (046)
                    puts.append((envelope, bound_etag))
        return ChatChange(self, tuple(puts), chat.scope, chat, actors, reused_actors=reused, reused_etags=etags,
                          played_from=played_from)

    def _scope_change(self, scope: ScopeRef, expected: str,
                      change: Callable[[PlayScope], PlayScope]) -> ChatChange:
        current, etag, stored = self._read_scope(scope)
        if etag != expected:
            raise StaleWrite([("/".join(address_parts(scope)), "changed")])
        return ChatChange(self, ((self._scope_record(change(current), stored), expected),), scope)


def _merged(reads: list[dict[ChatRef, str]]) -> dict[ChatRef, str] | None:
    """The chats a change's played revisions rest on, at the etags first read (a later read of the same chat
    at another etag means it moved during the plan, which the commit then finds), or ``None`` if none did."""
    if not reads:
        return None
    merged: dict[ChatRef, str] = {}
    for read in reads:
        for chat, etag in read.items():
            merged.setdefault(chat, etag)
    return merged


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
