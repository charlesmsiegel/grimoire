"""Store-backed accepted turn effects (Sprint 021): accept, revoke, wrap up and read continuity.

``TurnEffectsLibrary`` is trusted application code and is never handed to a worker. Its handle is
the **outcome key**: every acceptance, revocation and wrap-up is a director decision, committed as
one guarded store transaction that a human can ``preview`` and approve. A player-controlled
character's voluntary operation also needs the **authorship key**: the player's own 003 action
contribution and their endorsement (a trusted human handle plus the fingerprint of the operations
they were shown). The library only *checks* handles with ``issued``; by convention it never mints one.

An acceptance writes the accepted-event record and its 017 relation changes in **one** transaction,
so a reroll, an alternate response, a retry after a crash, a wrap-up or a later re-mention can never
apply it twice. Revocation is strictly latest-first and restores relations in place. Nothing here
writes 012/020 knowledge, evidence or beliefs, and no model is called.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace

from grimoire.core.authority import DIRECTOR, AuthorityError, PlayerContribution
from grimoire.core.identity import AcceptedRef, ChatRef, WrapUpRef, validate_segment
from grimoire.core.knowledge import Moment
from grimoire.core.model import ControlAssignment
from grimoire.effects import (
    ACCEPTED_SCHEMA, WRAPUP_SCHEMA, AcceptedEvent, AcceptedOperation, EffectCodecError, EffectError, EffectProblem,
    EffectProposal, EndorsementRecord, EndStep, Receipt, Revocation, SceneWrapUp, Skipped, accepted_from_payload,
    accepted_to_payload, accepted_token, authorize, check_citations, ledger_problems, orphans, plan,
    receipt_failure, revoked_token, verify_receipts, wrapup_from_payload, wrapup_id, wrapup_problems,
    wrapup_to_payload,
)
from grimoire.graph import Relation
from grimoire.graph.index import check_as_of, require_director
from grimoire.graphstore import CampaignGraph, InvalidRelation, RelationChange, RelationLibrary
from grimoire.library import LibraryPathError
from grimoire.security.policy import is_human
from grimoire.store import (
    LibraryStore, LoadProblem, RecordNotFound, ScopeRecord, StaleWrite, StoreError, StoredScopeRecord,
)
from grimoire.store.layout import address_parts

_ALLOCATION_ATTEMPTS = 16


class InvalidEffect(ValueError):
    """An effect change that cannot be made; nothing was written. ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str, records: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.reason = reason
        self.records = tuple(records)

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason, self.records))


def _refused(error: EffectError) -> InvalidEffect:
    return InvalidEffect(f"the effect change is refused: {error}", error.reason, error.records)


@dataclass(frozen=True)
class Endorsement:
    """A player's confirmation, from the player-input channel, that the operations with ``fingerprint``
    express their contribution ``contribution_id``. ``handle`` must be their trusted human handle."""

    contribution_id: str
    handle: object
    fingerprint: str


@dataclass(frozen=True)
class EffectCommit:
    etags: Mapping[str, str]
    ref: AcceptedRef | WrapUpRef


@dataclass(frozen=True)
class Continuity:
    """Director-only: what the next response may build on, and every disagreement found."""

    campaign_id: str
    as_of: int
    events: tuple[AcceptedEvent, ...]  # active, sequence <= as_of, by (sequence, ordinal)
    revoked: tuple[AcceptedEvent, ...]  # by ordinal
    wrapups: tuple[SceneWrapUp, ...]
    graph: CampaignGraph
    problems: tuple[EffectProblem, ...]
    store_problems: tuple[LoadProblem, ...]


@dataclass
class _Snapshot:
    """One campaign's accepted events, wrap-ups and graph, read under one store lock."""

    events: dict[AcceptedRef, AcceptedEvent]
    etags: dict[AcceptedRef, str]
    stored: dict[AcceptedRef, StoredScopeRecord]
    undecodable: list[EffectProblem]
    wrapups: dict[WrapUpRef, SceneWrapUp]
    wrapup_problems: list[EffectProblem]
    graph: CampaignGraph
    state: tuple
    store_problems: tuple[LoadProblem, ...]

    def active(self) -> list[AcceptedEvent]:
        return [event for event in self.events.values() if event.active]

    def relations(self) -> list[Relation]:
        index = self.graph.index
        found = []
        for ref in sorted(self.graph.etags, key=str):
            entry = index.relation(ref.relation_id, requested_by=DIRECTOR)
            if isinstance(entry.relation, Relation):
                found.append(entry.relation)
        return found


@dataclass
class EffectChange:
    """One store transaction, not yet committed: ``preview`` for a human, then ``commit``.

    ``duplicate_of`` is set when the same effect is already accepted: nothing will be written.
    """

    library: TurnEffectsLibrary
    ref: AcceptedRef | WrapUpRef
    puts: tuple[tuple[ScopeRecord, str | None], ...] = ()
    relation_changes: tuple[RelationChange, ...] = ()
    campaign_id: str = ""
    state: tuple = ()
    chat: ChatRef | None = None
    duplicate_of: AcceptedRef | None = None
    _committed: bool = field(default=False, repr=False)

    def _transaction(self):  # type: ignore[no-untyped-def]
        transaction = self.library.store.transaction()
        for change in self.relation_changes:
            for record, expected in change.puts:
                transaction.put(record, expected)
        for record, expected in self.puts:
            transaction.put(record, expected)
        return transaction

    def preview(self) -> dict[str, bytes]:
        """The exact bytes every file will hold, keyed by logical path; empty for a duplicate."""
        if self.duplicate_of is not None:
            return {}
        return self._transaction().preview()

    def stale(self) -> list[tuple[str, str]]:
        """Why this change can no longer be committed as previewed (call under the store lock)."""
        found = []
        for change in self.relation_changes:
            found += change.stale()
        if self.library._state(self.campaign_id) != self.state:
            found.append((f"campaigns/{self.campaign_id}/accepted", "accepted events changed"))
        if self.chat is not None and not self.library._chat_exists(self.chat):
            found.append(("/".join(address_parts(self.chat)), "chat missing"))
        return found

    def commit(self, approvals: Mapping[str, str] | None = None) -> EffectCommit:
        store = self.library.store
        with store.lock:  # nothing can write between these checks and the commit
            if self._committed:
                raise StoreError("this change was already committed")
            if self.duplicate_of is not None:  # still accepted, unchanged, and still applied?
                stale = self.library._stale_duplicate(self)
                if stale:
                    raise StaleWrite(stale)
                self._committed = True
                return EffectCommit({}, self.duplicate_of)
            stale = self.stale()
            if stale:
                raise StaleWrite(stale)
            result = self._transaction().commit(self.library.handle, approvals)
            self._committed = True
        return EffectCommit(result.etags, self.ref)


class _UniqueIds:
    """``new_id`` that never repeats a value within one change, even when the generator does."""

    def __init__(self, new_id: Callable[[str], str]) -> None:
        self._new_id = new_id
        self._taken: set[str] = set()

    def __call__(self, prefix: str) -> str:
        for _ in range(_ALLOCATION_ATTEMPTS):
            value = self._new_id(prefix)
            if value not in self._taken:
                self._taken.add(value)
                return value
        raise StoreError("could not allocate an unused id")


class TurnEffectsLibrary:
    """Accepted turn effects over one open, writable ``LibraryStore``."""

    def __init__(self, store: LibraryStore, handle: object, *, issued: Callable[[object], bool],
                 clock: Callable[[], str], new_id: Callable[[str], str], owner: str | None = None) -> None:
        if not callable(issued) or not (issued(handle) and getattr(handle, "trusted", False) is True):
            raise AuthorityError("the outcome key must be a trusted handle issued by this library's authorizer")
        self.store = store
        self.handle = handle
        self._issued = issued
        self._clock = clock
        self._new_id = new_id
        self._owner = owner

    # -- reading ------------------------------------------------------------------------------

    def read(self, ref: AcceptedRef, *, requested_by: str) -> tuple[AcceptedEvent, str]:
        """Director-only. The accepted event and its etag; store errors propagate."""
        require_director(requested_by)
        found = self.store.read(ref)
        try:
            return accepted_from_payload(ref, found.record.schema, found.record.payload), found.etag  # type: ignore[union-attr]
        except EffectCodecError as error:
            raise InvalidEffect(f"{ref} cannot be decoded: {error.reason}", "undecodable_event") from None

    def read_wrapup(self, ref: WrapUpRef, *, requested_by: str) -> tuple[SceneWrapUp, str]:
        """Director-only. The wrap-up and its etag; store errors propagate."""
        require_director(requested_by)
        found = self.store.read(ref)
        try:
            return wrapup_from_payload(ref, found.record.schema, found.record.payload), found.etag  # type: ignore[union-attr]
        except EffectCodecError as error:
            raise InvalidEffect(f"{ref} cannot be decoded: {error.reason}", "undecodable_wrapup") from None

    def continuity(self, campaign_id: str, *, as_of: int, requested_by: str) -> Continuity:
        """Director-only. The accepted events up to ``as_of``, the graph and every problem found."""
        require_director(requested_by)
        validate_segment(campaign_id, "campaign_id")
        as_of = check_as_of(as_of)
        snap = self._snapshot(campaign_id)
        events = list(snap.events.values())
        problems = list(snap.undecodable) + list(snap.wrapup_problems) + list(ledger_problems(events))
        for event in events:
            problems += verify_receipts(event, snap.graph.index)
        problems += orphans(events, snap.relations())
        problems += wrapup_problems(snap.wrapups.values(), events)
        active = sorted((e for e in events if e.active and e.moment.sequence <= as_of),
                        key=lambda e: (e.moment.sequence, e.ordinal, e.event_id))
        revoked = sorted((e for e in events if not e.active), key=lambda e: (e.ordinal, e.event_id))
        wrapups = tuple(snap.wrapups[ref] for ref in sorted(snap.wrapups, key=str))
        return Continuity(campaign_id, as_of, tuple(active), tuple(revoked), wrapups, snap.graph,
                          tuple(sorted(set(problems), key=EffectProblem.sort_key)), snap.store_problems)

    # -- accepting ----------------------------------------------------------------------------

    def accept(self, proposal: EffectProposal, *, controls: Iterable[ControlAssignment],
               contributions: Iterable[PlayerContribution] = (),
               endorsements: Iterable[Endorsement] = ()) -> EffectChange:
        """Make ``proposal`` playable truth once: preview, then commit. A repeat of an accepted effect is a no-op."""
        if type(proposal) is not EffectProposal:
            raise InvalidEffect("accept takes an EffectProposal", "invalid_field")
        campaign = proposal.campaign_id
        controls, endorsements = tuple(controls), tuple(endorsements)
        with self.store.lock:  # the whole decision is made over one consistent snapshot
            self._require_campaign(campaign)
            if not self._chat_exists(proposal.chat):
                raise InvalidEffect(f"{proposal.chat} does not exist", "missing_chat")
            try:
                authorization = authorize(proposal, controls, contributions)
            except EffectError as error:
                raise _refused(error) from None
            records = self._check_endorsements(authorization.endorsers, endorsements, proposal.fingerprint)
            snap = self._snapshot(campaign)
            self._require_trustworthy(snap)
            if any(problem.code == "undecodable" for problem in snap.graph.index.problems):
                raise InvalidEffect("a relation file cannot be read, so preconditions cannot be checked",
                                    "undecodable_relation")
            wanted = proposal.fingerprint
            for event in snap.active():
                if event.fingerprint == wanted:
                    if verify_receipts(event, snap.graph.index):
                        raise InvalidEffect(f"{event.ref} is accepted but its receipts do not match the relations",
                                            "receipt_mismatch", (event.event_id,))
                    return EffectChange(self, event.ref, campaign_id=campaign, state=snap.state,
                                        duplicate_of=event.ref)
            for event in snap.active():
                if verify_receipts(event, snap.graph.index):
                    raise InvalidEffect(f"{event.ref} does not match the relations; repair it first",
                                        "receipt_mismatch", (event.event_id,))
            floor = max((e.moment.sequence for e in snap.active()), default=0)
            if proposal.moment.sequence < floor:
                raise InvalidEffect(f"sequence {proposal.moment.sequence} is behind an accepted event at {floor}",
                                    "stale_sequence")
            try:
                steps = plan(proposal, snap.graph.index)
            except EffectError as error:
                raise _refused(error) from None
            ref = self._allocate(campaign)
            token = accepted_token(ref.event_id)
            relations = self._relations()
            changes, receipts = [], []
            for step, op in zip(steps, proposal.operations):
                try:
                    if isinstance(step, EndStep):
                        change = relations.end(step.relation, until=proposal.moment, provenance=token,
                                               expected=snap.graph.etags[step.relation])
                    else:
                        change = relations.add(campaign, op.predicate, op.subject, op.object, since=proposal.moment,
                                               provenance=token)
                except InvalidRelation as error:
                    reason = "precondition_failed" if error.reason == "duplicate_relation" else error.reason
                    raise InvalidEffect(f"operation {step.op} is refused: {error.reason}", reason,
                                        (str(step.op),)) from None
                changes.append(change)
                receipts.append(Receipt(step.op, change.relation))
            ordinal = 1 + max((e.ordinal for e in snap.events.values()), default=0)
            try:
                event = AcceptedEvent(
                    ref, proposal.chat, proposal.moment, proposal.summary, proposal.proposed_by,
                    tuple(AcceptedOperation(op, domain) for op, domain in zip(proposal.operations,
                                                                               authorization.domains)),
                    authorization.contributions, records, authorization.controls, str(self.handle.principal),  # type: ignore[attr-defined]
                    self._clock(), ordinal, tuple(receipts))
            except EffectError as error:
                raise _refused(error) from None
            record = ScopeRecord(ref, ACCEPTED_SCHEMA, accepted_to_payload(event), owner=self._owner)
            return EffectChange(self, ref, ((record, None),), tuple(changes), campaign, snap.state, proposal.chat)

    # -- revoking -----------------------------------------------------------------------------

    def revoke(self, ref: AcceptedRef, *, reason: str, expected: str, endorsements: Iterable[Endorsement] = (),
               partial: bool = False) -> EffectChange:
        """Undo the latest accepted event: retract what it began, reopen what it ended, tombstone it.

        Needs the outcome key and the original endorsers. ``partial=True`` is the repair path: operations
        whose receipts no longer match (or that something now depends on) are left alone and recorded.
        """
        if type(ref) is not AcceptedRef:
            raise InvalidEffect("revoke takes an AcceptedRef", "invalid_field")
        if type(partial) is not bool:
            raise InvalidEffect("partial must be a bool", "invalid_field")
        endorsements = tuple(endorsements)
        campaign = ref.campaign_id
        with self.store.lock:
            snap = self._snapshot(campaign)
            self._require_trustworthy(snap)
            if ref not in snap.events:
                self.store.read(ref)  # raises the store's own error (RecordNotFound ...)
                raise InvalidEffect(f"{ref} cannot be read", "undecodable_event")
            event, etag = snap.events[ref], snap.etags[ref]
            if etag != expected:
                raise StaleWrite([("/".join(address_parts(ref)), "changed")])
            if not event.active:
                raise InvalidEffect(f"{ref} is already revoked", "already_revoked")
            later = [e.event_id for e in snap.active() if e.ordinal > event.ordinal]
            if later:
                raise InvalidEffect(f"{ref} is not the latest accepted event", "not_latest", sorted(later))
            needed = {item.contribution_id: item.principal for item in event.endorsements}
            records = self._check_endorsements(needed, endorsements, event.fingerprint)
            relations = self._relations()
            token = revoked_token(ref.event_id)
            changes, skipped = [], []
            for number, item in enumerate(event.operations):
                why = receipt_failure(event, number, snap.graph.index)
                relation_ref = event.receipts[number].relation
                change = None
                if why is None:
                    relation = snap.graph.index.relation(relation_ref.relation_id, requested_by=DIRECTOR).relation
                    if item.op.kind == "begin" and relation.ending is not None:  # type: ignore[union-attr]
                        why = "has_dependents: ended since"
                    else:
                        try:
                            if item.op.kind == "begin":
                                change = relations.retract(relation_ref, provenance=token,
                                                           expected=snap.graph.etags[relation_ref])
                            else:
                                change = relations.reopen(relation_ref, provenance=token,
                                                          expected=snap.graph.etags[relation_ref])
                        except InvalidRelation as error:
                            why = f"has_dependents: {error.reason}"
                elif not partial:
                    raise InvalidEffect(f"operation {number} of {ref} does not match the relations: {why}",
                                        "receipt_mismatch", (str(number),))
                if change is None:
                    if not partial:
                        raise InvalidEffect(f"operation {number} of {ref}: {why}", "has_dependents", (str(number),))
                    skipped.append(Skipped(number, why))  # type: ignore[arg-type]
                    continue
                changes.append(change)
            try:
                revocation = Revocation(reason, self._clock(), str(self.handle.principal), records,  # type: ignore[attr-defined]
                                        tuple(skipped))
                revoked = replace(event, revocation=revocation)
            except EffectError as error:
                raise _refused(error) from None
            stored = snap.stored[ref].record
            record = ScopeRecord(ref, ACCEPTED_SCHEMA, accepted_to_payload(revoked), owner=stored.owner,
                                 extra=stored.extra)
            return EffectChange(self, ref, ((record, expected),), tuple(changes), campaign, snap.state)

    # -- wrapping up --------------------------------------------------------------------------

    def wrap_up(self, chat: ChatRef, scene_id: str, *, through: Moment, summary: str, cites: Iterable[str],
                written_by: str, requested_by: str, expected: str | None = None) -> EffectChange:
        """Director-only. The scene's retrospective summary: it must cite every accepted event of the scene up to
        ``through`` and changes nothing else. ``expected`` replaces an existing wrap-up (CAS)."""
        require_director(requested_by)
        if type(chat) is not ChatRef:
            raise InvalidEffect("wrap_up takes a ChatRef", "invalid_field")
        campaign = chat.campaign_id
        with self.store.lock:
            self._require_campaign(campaign)
            if not self._chat_exists(chat):
                raise InvalidEffect(f"{chat} does not exist", "missing_chat")
            snap = self._snapshot(campaign)
            if snap.undecodable:
                raise InvalidEffect("an accepted-event file cannot be read, so citations cannot be checked",
                                    "undecodable_event")
            if isinstance(cites, str):
                raise InvalidEffect("cites must be an iterable of event ids, not text", "invalid_field")
            cites = tuple(sorted(set(cites)))
            try:
                ref = WrapUpRef(campaign, wrapup_id(chat, scene_id))
                if type(through) is not Moment:
                    raise EffectError("invalid_field")
                check_citations(cites, snap.events.values(), chat, scene_id, through)
                wrapup = SceneWrapUp(ref, chat, scene_id, through, summary, cites, written_by,  # type: ignore[arg-type]
                                     self._clock())
            except EffectError as error:
                raise _refused(error) from None
            owner, extra = self._owner, {}
            if expected is not None:
                try:
                    current = self.store.read(ref).record
                    owner, extra = current.owner, current.extra
                except (StoreError, LibraryPathError):
                    pass  # the store's CAS reports it
            record = ScopeRecord(ref, WRAPUP_SCHEMA, wrapup_to_payload(wrapup), owner=owner, extra=extra)
            return EffectChange(self, ref, ((record, expected),), (), campaign, snap.state, chat)

    # -- helpers -------------------------------------------------------------------------------

    def _relations(self) -> RelationLibrary:
        return RelationLibrary(self.store, self.handle, clock=self._clock, new_id=_UniqueIds(self._new_id),
                               owner=self._owner)

    def _require_campaign(self, campaign_id: str) -> None:
        validate_segment(campaign_id, "campaign_id")
        if self.store.campaign_presence(campaign_id) != "present":  # a look-alike folder is not the campaign
            raise InvalidEffect(f"campaign {campaign_id} does not exist", "missing_campaign")

    def _chat_exists(self, chat: ChatRef) -> bool:
        try:
            self.store.read(chat)
        except (StoreError, LibraryPathError, ValueError):
            return False
        return True

    def _check_endorsements(self, needed: Mapping[str, str], given: tuple, wanted: str
                            ) -> tuple[EndorsementRecord, ...]:
        by_id: dict[str, Endorsement] = {}
        for item in given:
            if type(item) is not Endorsement:
                raise InvalidEffect("endorsements must be Endorsement values", "invalid_endorsement")
            by_id.setdefault(item.contribution_id, item)
        records = []
        for contribution_id, principal in sorted(needed.items()):
            item = by_id.get(contribution_id)
            if item is None:
                raise InvalidEffect(f"contribution {contribution_id} needs its player's endorsement", "not_endorsed",
                                    (contribution_id,))
            handle = item.handle
            if not (self._issued(handle) and getattr(handle, "trusted", False) is True
                    and is_human(getattr(handle, "principal", None)) and handle.principal == principal  # type: ignore[attr-defined]
                    and item.fingerprint == wanted):
                raise InvalidEffect(f"the endorsement of {contribution_id} is not valid", "invalid_endorsement",
                                    (contribution_id,))
            records.append(EndorsementRecord(contribution_id, principal, wanted))
        return tuple(records)

    def _allocate(self, campaign_id: str) -> AcceptedRef:
        for _ in range(_ALLOCATION_ATTEMPTS):
            value = self._new_id("e")
            validate_segment(value, "generated id")
            ref = AcceptedRef(campaign_id, value)
            try:
                self.store.read(ref)
            except RecordNotFound:  # exact presence only: a look-alike fails the commit with NameCollision
                return ref
            except (StoreError, LibraryPathError):
                continue
        raise StoreError("could not allocate an unused accepted-event id")

    def _stale_duplicate(self, change: EffectChange) -> list[tuple[str, str]]:
        """A duplicate decision rests on the event being active, unchanged and applied; re-check all three."""
        path = "/".join(address_parts(change.duplicate_of))  # type: ignore[arg-type]
        if self._state(change.campaign_id) != change.state:
            return [(path, "accepted events changed")]
        snap = self._snapshot(change.campaign_id)
        event = snap.events.get(change.duplicate_of)  # type: ignore[arg-type]
        if event is None or not event.active or verify_receipts(event, snap.graph.index):
            return [(path, "no longer accepted as it was")]
        return []

    def _state(self, campaign_id: str) -> tuple:
        found = self.store.campaign_accepted(campaign_id)
        return (tuple(sorted((str(ref), stored.etag) for ref, stored in found.records.items())),
                tuple(sorted((p.path, p.code) for p in found.problems)))

    def _snapshot(self, campaign_id: str) -> _Snapshot:
        with self.store.lock:
            accepted = self.store.campaign_accepted(campaign_id)
            wrapped = self.store.campaign_wrapups(campaign_id)
            graph = self._relations().graph(campaign_id, requested_by=DIRECTOR)
            state = self._state(campaign_id)
        events, etags, stored, undecodable = {}, {}, {}, []
        for ref, found in accepted.records.items():
            etags[ref], stored[ref] = found.etag, found
            try:
                events[ref] = accepted_from_payload(ref, found.record.schema, found.record.payload)
            except EffectCodecError as error:
                undecodable.append(EffectProblem("undecodable_event", ref.event_id, detail=error.reason))
        for problem in accepted.problems:
            undecodable.append(EffectProblem("undecodable_event", detail=f"{problem.code}: {problem.path}"))
        wrapups, wrapup_issues = {}, []
        for ref, found in wrapped.records.items():
            try:
                wrapups[ref] = wrapup_from_payload(ref, found.record.schema, found.record.payload)
            except EffectCodecError as error:
                wrapup_issues.append(EffectProblem("undecodable_wrapup", wrapup_id=ref.wrapup_id, detail=error.reason))
        for problem in wrapped.problems:
            wrapup_issues.append(EffectProblem("undecodable_wrapup", detail=f"{problem.code}: {problem.path}"))
        return _Snapshot(events, etags, stored, undecodable, wrapups, wrapup_issues, graph, state,
                         accepted.problems + wrapped.problems)

    def _require_trustworthy(self, snap: _Snapshot) -> None:
        """An unreadable accepted-event file or an inconsistent stack blocks accept and revoke (fail closed)."""
        if snap.undecodable:
            raise InvalidEffect("an accepted-event file cannot be read; fix it or move it out of the library",
                                "undecodable_event", tuple(p.detail for p in snap.undecodable))
        if ledger_problems(snap.events.values()):
            raise InvalidEffect("the accepted-event stack is inconsistent (duplicate ordinals or order)",
                                "inconsistent_ledger")

