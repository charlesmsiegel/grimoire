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
writes 012/020 knowledge, evidence or beliefs, and no model is called. ``continuity`` reads 020's raw
event files (through the store, with 020's pure codec, never its service) only to check the bridge.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace

from grimoire.core.authority import DIRECTOR, AuthorityError, PlayerContribution
from grimoire.core.identity import (
    AcceptedRef, ChatRef, InstanceRef, KnowledgeRef, WrapUpRef, paired_instances, validate_segment,
)
from grimoire.core.knowledge import Event, Moment
from grimoire.core.model import ControlAssignment
from grimoire.effects import (
    WRAPUP_SCHEMA, AcceptedEvent, AcceptedOperation, EffectCodecError, EffectError, EffectProblem, EffectProposal,
    EndorsementRecord, EndStep, Op, Receipt, Revocation, SceneWrapUp, Skipped, accepted_from_payload, accepted_schema,
    accepted_to_payload, accepted_token, authorize, bridge_problems, check_citations, edited_effects, holdings_orphans,
    holdings_partials, holdings_problems, ledger_problems, orphans, parse_token, partial_revocations, plan,
    receipt_failure, revocation_fingerprint, revoked_token, verify_receipts, wrapup_from_payload, wrapup_id,
    wrapup_problems, wrapup_to_payload,
)
from grimoire.evidence import EvidenceCodecError, decode
from grimoire.graph import Relation
from grimoire.graph.codec import RelationCodecError, relation_from_payload
from grimoire.graph.index import check_as_of, require_director
from grimoire.graphstore import CampaignGraph, InvalidRelation, RelationChange, RelationLibrary
from grimoire.holdingstore import HoldingsChange, HoldingsLibrary
from grimoire.library import LibraryPathError
from grimoire.security.policy import is_human
from grimoire.store import (
    KnowledgeScan, LibraryStore, LinkRefused, LoadProblem, RecordNotFound, ScopeRecord, StaleWrite, StoreError,
    StoredScopeRecord, TakenIdsUnlisted,
)
from grimoire.store.layout import address_parts

from . import parts
from .errors import InvalidEffect
from .errors import refused as _refused

_ALLOCATION_ATTEMPTS = 16


def _actors(op: Op) -> frozenset[InstanceRef]:
    """The individuals an operation names: its endpoints and its agent."""
    return frozenset(ref for ref in (op.subject, op.object, op.agent) if ref is not None)


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
    checks_relations: bool = False  # an acceptance: its preconditions need every relation file readable
    agent_etags: Mapping = field(default_factory=dict)  # an acceptance: each agent's instance file as checked
    actors: frozenset = frozenset()  # an acceptance: every endpoint and agent, re-checked for an id pair
    rechecks_readability: bool = False  # a revocation: every relation file must still be readable at commit
    holdings_changes: tuple[HoldingsChange, ...] = ()  # the holder files, folded into this transaction
    _committed: bool = field(default=False, repr=False)

    def _transaction(self):  # type: ignore[no-untyped-def]
        transaction = self.library.store.transaction()
        for change in self.relation_changes:
            for record, expected in change.puts:
                transaction.put(record, expected)
        for record, expected in self.puts:
            transaction.put(record, expected)
        for holdings in self.holdings_changes:
            for record, expected in holdings.puts:
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
        relations, unreadable = _relation_files(self.library.store, self.campaign_id)  # one read for every check
        for change in self.relation_changes:
            found += change.stale(relations)
        if self.library._state(self.campaign_id) != self.state:
            found.append((f"campaigns/{self.campaign_id}/accepted", "accepted events changed"))
        if self.chat is not None and not self.library._chat_exists(self.chat):
            found.append(("/".join(address_parts(self.chat)), "chat missing"))
        if (self.checks_relations or self.rechecks_readability) and unreadable:
            found.append((f"campaigns/{self.campaign_id}/relations", "a relation file cannot be read"))
        for agent, etag in self.agent_etags.items():  # a replaced file may be a different individual
            if self.library._etag(agent) != etag:
                found.append(("/".join(address_parts(agent)), "agent missing or changed"))
        if self.actors:  # another kind may have taken an actor's id since: no one individual (IDENTITY.md)
            paired = paired_instances(self.library.store.campaign_instances(self.campaign_id))
            found += [("/".join(address_parts(actor)), "instance_id_conflict")
                      for actor in sorted(self.actors & paired, key=str)]
        event_id = getattr(self.ref, "event_id", None)
        if self.checks_relations and not self.library._absent(KnowledgeRef(self.campaign_id, "event", event_id)):
            found.append((f"campaigns/{self.campaign_id}/events/{event_id}.json",
                          "a 020 event took this id"))
        return list(dict.fromkeys(found))  # an endpoint's pair is also reported by its 017 change

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
            # Each holder still the individual it was planned against: first, so a holder replaced since the plan
            # is HolderReplaced (never retried through) even when it is also a pinned agent.
            parts.recheck(self.holdings_changes)
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
        as_of = check_as_of(as_of)
        self._require_campaign(campaign_id)  # a mistyped campaign is an error, never an empty "all clear"
        with self.store.lock:  # the bridges and holder files are read with the same snapshot
            snap = self._snapshot(campaign_id)
            bridges = _stored_events(self.store.campaign_knowledge(campaign_id))
            held, decoded, damaged = parts.campaign_holders(self._holdings(), campaign_id, snap.events.values())
        events = list(snap.events.values())
        problems = list(snap.undecodable) + list(snap.wrapup_problems) + list(ledger_problems(events))
        problems += bridge_problems(events, bridges)
        for event in events:
            problems += verify_receipts(event, snap.graph.index)
        problems += orphans(events, snap.relations())
        problems += edited_effects(events, snap.graph.index)
        problems += partial_revocations(events, snap.graph.index)
        problems += damaged + list(holdings_problems(events, held))
        problems += holdings_orphans(events, decoded)
        problems += holdings_partials(events)
        problems += wrapup_problems(snap.wrapups.values(), events)
        active = sorted((e for e in events if e.active and e.moment.sequence <= as_of),
                        key=lambda e: (e.moment.sequence, e.ordinal, e.event_id))
        revoked = sorted((e for e in events if not e.active and e.moment.sequence <= as_of),  # never from after as_of
                         key=lambda e: (e.ordinal, e.event_id))
        wrapups = tuple(snap.wrapups[ref] for ref in sorted(snap.wrapups, key=str)  # never narration from after as_of
                        if snap.wrapups[ref].through.sequence <= as_of)
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
            snap = self._snapshot(campaign)
            self._require_trustworthy(snap)
            unreadable = _relations_unreadable(snap.graph)
            wanted = proposal.fingerprint
            for event in snap.active():
                # A duplicate writes nothing, so it needs no keys: a retry after control changed hands, or with a
                # stale endorsement, is still told that the effect is in force rather than refused for authority.
                # Its receipts name exact relation files, so damage elsewhere does not refuse it (#586 follow-up);
                # when they fail while a file is damaged, the damage is named, never a mismatch.
                if event.fingerprint == wanted:
                    if verify_receipts(event, snap.graph.index):
                        if unreadable:
                            raise InvalidEffect("a relation file cannot be read, so the accepted effect cannot be "
                                                "checked", "undecodable_relation")
                        raise InvalidEffect(f"{event.ref} is accepted but its receipts do not match the relations",
                                            "receipt_mismatch", (event.event_id,))
                    if parts.duplicate_holdings(event, self._holdings()) is not None:
                        raise InvalidEffect(f"{event.ref} is accepted but its holdings do not match the holders",
                                            "receipt_mismatch", (event.event_id,))
                    return EffectChange(self, event.ref, campaign_id=campaign, state=snap.state,
                                        duplicate_of=event.ref)
            parts.check_permitted(proposal)  # after the duplicate check: a retry of a charged use stays a no-op
            if unreadable:  # a new effect's preconditions could be hidden by any damaged relation file
                raise InvalidEffect("a relation file cannot be read, so preconditions cannot be checked",
                                    "undecodable_relation")
            paired = paired_instances(self.store.campaign_instances(campaign))  # load() leaves both members out
            clash = [str(number) for number, op in enumerate(proposal.operations) if _actors(op) & paired]
            clash += sorted(str(actor) for actor in parts.holdings_actors(proposal) & paired)
            if clash:
                raise InvalidEffect("an operation names an actor whose id another kind also uses",
                                    "instance_id_conflict", clash)
            try:
                authorization = authorize(proposal, controls, contributions)
            except EffectError as error:
                raise _refused(error) from None
            records = self._check_endorsements(authorization.endorsers, endorsements, proposal.fingerprint)
            turn = (proposal.chat, proposal.moment.scene_id, proposal.moment.turn_id)
            same_turn = [e.event_id for e in snap.active()
                         if (e.chat, e.moment.scene_id, e.moment.turn_id) == turn]
            if same_turn:  # one accepted effect per turn: another swipe's effect needs a revocation first
                raise InvalidEffect(f"turn {proposal.moment.turn_id} already has an accepted effect",
                                    "turn_already_accepted", same_turn)
            floor = max((e.moment.sequence for e in snap.active()), default=0)
            if proposal.moment.sequence < floor:
                raise InvalidEffect(f"sequence {proposal.moment.sequence} is behind an accepted event at {floor}",
                                    "stale_sequence")
            parts.check_attempts(proposal, snap.events.values(), self._holdings())
            try:
                steps = plan(proposal, snap.graph.index)
            except EffectError as error:
                raise _refused(error) from None
            held_tokens, held_unread = parts.holder_tokens(self.store, campaign)
            if held_unread and proposal.holdings is not None:  # it could name an id, or hide a precondition
                raise InvalidEffect("a holder file cannot be read, so the holdings cannot be checked",
                                    "undecodable_holdings")
            ref = self._allocate(campaign, _token_ids(self.store, campaign) | held_tokens)
            token = accepted_token(ref.event_id)
            relations = self._relations()
            known = snap.relations()  # decoded once, under this lock; no per-operation re-read
            changes, receipts = [], []
            for step, op in zip(steps, proposal.operations):
                try:
                    if isinstance(step, EndStep):
                        change = relations.end(step.relation, until=proposal.moment, provenance=token,
                                               expected=snap.graph.etags[step.relation])
                    else:
                        change = relations.add(campaign, op.predicate, op.subject, op.object, since=proposal.moment,
                                               provenance=token, relations=known)
                except InvalidRelation as error:
                    reason = "precondition_failed" if error.reason == "duplicate_relation" else error.reason
                    raise InvalidEffect(f"operation {step.op} is refused: {error.reason}", reason,
                                        (str(step.op),)) from None
                changes.append(change)
                receipts.append(Receipt(step.op, change.relation))
            planned = parts.plan_holdings(self._holdings(), proposal, snap.events.values(), ref.event_id,
                                          authorization.holdings_domains)
            charged = parts.charges(proposal, authorization.ability_domains)
            ordinal = 1 + max((e.ordinal for e in snap.events.values()), default=0)
            try:
                event = AcceptedEvent(
                    ref, proposal.chat, proposal.moment, proposal.summary, proposal.proposed_by,
                    tuple(AcceptedOperation(op, domain) for op, domain in zip(proposal.operations,
                                                                               authorization.domains)),
                    authorization.contributions, records, authorization.controls, str(self.handle.principal),  # type: ignore[attr-defined]
                    self._clock(), ordinal, tuple(receipts), attempt_id=proposal.attempt_id,
                    holdings=None if planned is None else planned[1], abilities=charged)
            except EffectError as error:
                raise _refused(error) from None
            record = ScopeRecord(ref, accepted_schema(event), accepted_to_payload(event), owner=self._owner)
            acting = {op.agent for op in proposal.operations if op.agent is not None} | parts.acting_agents(proposal)
            agents = {agent: self._etag(agent) for agent in sorted(acting, key=str)}
            actors = frozenset().union(*(_actors(op) for op in proposal.operations)) | parts.holdings_actors(proposal)
            return EffectChange(self, ref, ((record, None),), tuple(changes), campaign, snap.state, proposal.chat,
                                checks_relations=True, agent_etags=agents, actors=actors,
                                holdings_changes=() if planned is None else (planned[0],))

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
            if _relations_unreadable(snap.graph):  # a hidden relation could be overlapped by a reopen, or be a receipt
                raise InvalidEffect("a relation file cannot be read, so the undo cannot be checked",
                                    "undecodable_relation")
            if ref not in snap.events:
                self.store.read(ref)  # raises the store's own error (RecordNotFound ...)
                raise InvalidEffect(f"{ref} cannot be read", "undecodable_event")
            event, etag = snap.events[ref], snap.etags[ref]
            if not event.active:  # before the CAS, so a retry of a revoke that landed is told so
                raise InvalidEffect(f"{ref} is already revoked", "already_revoked")
            if etag != expected:
                raise StaleWrite([("/".join(address_parts(ref)), "changed")])
            later = [e.event_id for e in snap.active() if e.ordinal > event.ordinal]
            if later:
                raise InvalidEffect(f"{ref} is not the latest accepted event", "not_latest", sorted(later))
            needed = {item.contribution_id: item.principal for item in event.endorsements}
            records = self._check_endorsements(needed, endorsements,
                                               revocation_fingerprint(event.ref, event.fingerprint))
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
                                                          expected=snap.graph.etags[relation_ref],
                                                          relations=snap.relations())
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
            undo, undo_seqs, holdings_skipped = parts.plan_undo(self._holdings(), event, partial)
            try:
                revocation = Revocation(reason, self._clock(), str(self.handle.principal), records,  # type: ignore[attr-defined]
                                        tuple(skipped), holdings_skipped, undo_seqs)
                revoked = replace(event, revocation=revocation)
            except EffectError as error:
                raise _refused(error) from None
            stored = snap.stored[ref].record
            record = ScopeRecord(ref, accepted_schema(revoked), accepted_to_payload(revoked), owner=stored.owner,
                                 extra=stored.extra)
            return EffectChange(self, ref, ((record, expected),), tuple(changes), campaign, snap.state,
                                rechecks_readability=True, holdings_changes=() if undo is None else (undo,))

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
            try:
                cites = None if isinstance(cites, str) else tuple(cites)  # type: ignore[arg-type]
            except TypeError:
                cites = None  # type: ignore[assignment]
            if cites is None or not all(type(cite) is str for cite in cites):
                raise InvalidEffect("cites must be an iterable of event ids (text)", "invalid_field")
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

    def _holdings(self) -> HoldingsLibrary:
        return HoldingsLibrary(self.store, self.handle, clock=self._clock, owner=self._owner)

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
            if item.contribution_id in by_id:  # two credentials for one contribution: ambiguous, never pick one
                raise InvalidEffect(f"contribution {item.contribution_id} has more than one endorsement",
                                    "invalid_endorsement", (item.contribution_id,))
            by_id[item.contribution_id] = item
        records = []
        for contribution_id, principal in sorted(needed.items()):
            if not is_human(principal):  # only an authenticated human authors a PC's action (003/010b)
                raise InvalidEffect(f"contribution {contribution_id}'s controller is not a human: principal",
                                    "non_human_controller", (contribution_id,))
            item = by_id.get(contribution_id)
            if item is None:
                raise InvalidEffect(f"contribution {contribution_id} needs its player's endorsement", "not_endorsed",
                                    (contribution_id,))
            handle = item.handle
            if not (self._issued(handle) and getattr(handle, "trusted", False) is True
                    and is_human(getattr(handle, "principal", None))
                    and handle.principal == principal  # type: ignore[attr-defined]
                    and item.fingerprint == wanted):
                raise InvalidEffect(f"the endorsement of {contribution_id} is not valid", "invalid_endorsement",
                                    (contribution_id,))
            records.append(EndorsementRecord(contribution_id, principal, wanted))
        return tuple(records)

    def _allocate(self, campaign_id: str, tokened: frozenset[str] = frozenset()) -> AcceptedRef:
        """An unused event id: no accepted-event file, no 020 event file, no relation or holder token naming it."""
        for _ in range(_ALLOCATION_ATTEMPTS):
            value = self._new_id("e")
            validate_segment(value, "generated id")
            ref = AcceptedRef(campaign_id, value)
            if not self._absent(ref) or not self._absent(KnowledgeRef(campaign_id, "event", value)):
                continue  # also never a 020 event id, so the 012 bridge can later be stored under the same id
            if value in tokened:
                continue  # a hand-deleted event's orphaned relation changes must never be adopted by a new event
            return ref
        raise StoreError("could not allocate an unused accepted-event id")

    def _etag(self, ref) -> str | None:  # type: ignore[no-untyped-def]
        try:
            return self.store.read(ref).etag
        except (StoreError, LibraryPathError, ValueError):
            return None

    def _absent(self, ref: AcceptedRef | KnowledgeRef) -> bool:
        """Exact presence only: a look-alike fails the commit with NameCollision; an unreadable file counts.

        A linked or unlistable folder (a symlinked ``events/``) cannot say whether the id is free, so it
        raises ``TakenIdsUnlisted`` rather than counting every id as taken until allocation gives up.
        """
        try:
            self.store.read(ref)
        except RecordNotFound:
            return True
        except (LinkRefused, OSError) as error:
            folder = "/".join(address_parts(ref)[:-1])
            raise TakenIdsUnlisted(f"{folder} cannot be read ({type(error).__name__}), so it cannot be told "
                                   "whether an id is free", (folder,)) from error
        except (StoreError, LibraryPathError, ValueError):
            return False
        return False

    def _stale_duplicate(self, change: EffectChange) -> list[tuple[str, str]]:
        """A duplicate decision rests on the event being active, unchanged and applied; re-check all three."""
        path = "/".join(address_parts(change.duplicate_of))  # type: ignore[arg-type]
        if self._state(change.campaign_id) != change.state:
            return [(path, "accepted events changed")]
        snap = self._snapshot(change.campaign_id)
        event = snap.events.get(change.duplicate_of)  # type: ignore[arg-type]
        if event is None or not event.active or verify_receipts(event, snap.graph.index):
            return [(path, "no longer accepted as it was")]
        try:
            if parts.duplicate_holdings(event, self._holdings()) is not None:
                return [(path, "its holdings no longer match")]
        except InvalidEffect:
            return [(path, "a holder file cannot be read")]
        return []

    def _graph(self, campaign_id: str) -> CampaignGraph:
        return self._relations().graph(campaign_id, requested_by=DIRECTOR)

    def _state(self, campaign_id: str) -> tuple:
        return _state_of(self.store.campaign_accepted(campaign_id))

    def _snapshot(self, campaign_id: str) -> _Snapshot:
        with self.store.lock:
            accepted = self.store.campaign_accepted(campaign_id)
            wrapped = self.store.campaign_wrapups(campaign_id)
            graph = self._relations().graph(campaign_id, requested_by=DIRECTOR)
        state = _state_of(accepted)  # from the very read the decision uses, never a second one
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


def _relations_unreadable(graph: CampaignGraph) -> bool:
    """A relation file that cannot be decoded, or any store problem under the campaign's relations
    (a link, a look-alike folder), could hide the fact a precondition must see."""
    return bool(graph.problems) or any(problem.code == "undecodable" for problem in graph.index.problems)


def _state_of(found) -> tuple:  # type: ignore[no-untyped-def]
    """The accepted-event files and problems of one read, as a comparable value."""
    return (tuple(sorted((str(ref), stored.etag) for ref, stored in found.records.items())),
            tuple(sorted((p.path, p.code) for p in found.problems)))


def _relation_files(store: LibraryStore, campaign_id: str) -> tuple[list[Relation], bool]:
    """One read of a campaign's relation files: the decodable relations, and whether any file (or the
    folder) could not be read."""
    found = store.campaign_relations(campaign_id)
    decoded, unreadable = [], bool(found.problems)
    for ref, stored in found.records.items():
        try:
            relation = relation_from_payload(ref, stored.record.schema, stored.record.payload)  # type: ignore[arg-type]
        except RelationCodecError:
            unreadable = True
            continue
        if isinstance(relation, Relation):
            decoded.append(relation)
    return decoded, unreadable


def _stored_events(scan: KnowledgeScan) -> dict[str, Event | str]:
    """The campaign's 020 event files, decoded with 020's pure codec (never its service): id → ``Event``, or
    why the file cannot be read. Raw files, not the assembled ledger, so an unlisted one still counts."""
    found: dict[str, Event | str] = {}
    for ref, stored in scan.records.items():
        if ref.family != "event":
            continue
        try:
            record, _ = decode(ref, stored.record.schema, stored.record.payload)
        except EvidenceCodecError as error:
            found[ref.record_id] = error.reason
            continue
        found[ref.record_id] = record  # type: ignore[assignment]
    for ref, code in scan.damaged.items():
        if ref.family == "event":
            found[ref.record_id] = code
    return found


def _token_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    """Event ids named by a 021 token (``effect:accepted/<id>``, ``effect:revoked/<id>``) anywhere in the raw
    payload of one of the campaign's relation files (provenance, endings, retractions, reopenings, unknown keys)."""
    found: set[str] = set()
    for stored in store.campaign_relations(campaign_id).records.values():
        _collect_tokens(stored.record.payload, found)
    return frozenset(found)


def _collect_tokens(value: object, into: set[str]) -> None:
    if isinstance(value, str):
        token = parse_token(value)
        if token is not None:
            into.add(token[1])
    elif isinstance(value, Mapping):
        for item in value.values():
            _collect_tokens(item, into)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_tokens(item, into)


def accepted_instance_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    """Instance ids named by the campaign's accepted-event files: every instance reference of this campaign
    in a payload (operation subjects, objects, agents) and every recorded ``actor_id``.

    Like 017's ``relation_endpoint_ids``, every instance allocator must treat these as taken, so a
    hand-deleted individual's id never goes to a different one that would silently inherit its past
    effects. It scans the raw payloads without decoding them, so an event that fails validation still
    protects its ids. A file or folder the store cannot read at all (malformed, a link, a look-alike)
    may name ids too, so it raises ``TakenIdsUnlisted``: allocation fails closed rather than skip it.
    It reads every accepted-event file of the campaign: an allocator's cost grows with play history.
    """
    found = store.campaign_accepted(campaign_id)
    if found.problems:
        paths = tuple(sorted({problem.path for problem in found.problems}))
        raise TakenIdsUnlisted(f"accepted-event files of {campaign_id} cannot all be read ({', '.join(paths)}), so "
                               "the instance ids they name cannot be reserved", paths)
    taken: set[str] = set()
    for stored in found.records.values():
        _collect_ids(stored.record.payload, campaign_id, taken)
    return frozenset(taken)


def _collect_ids(value: object, campaign_id: str, into: set[str]) -> None:
    if isinstance(value, str):
        try:
            ref = InstanceRef.parse(value)
        except ValueError:
            return
        if ref.campaign_id == campaign_id:
            into.add(ref.instance_id)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if key == "actor_id" and isinstance(item, str):
                into.add(item)
            else:
                _collect_ids(item, campaign_id, into)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_ids(item, campaign_id, into)
