"""Store-backed party membership (Sprint 033): join, store, withdraw, leave, release and move, durably.

``PartyLibrary`` is trusted application code and is never handed to a worker. A move ends the member's
current membership relation and adds the new one in **one** store transaction (``PartyChange``: preview,
then commit), through 017's ``RelationLibrary``, so a member is never in two places and never lost between
them. Nothing is deleted: ending a relation keeps it on disk, which is the membership history, and the
member's instance file, relations, events, evidence and knowledge are untouched by every operation here.

Membership is director truth written through the store's preview and approval, like 017's relations;
moves made in play go through 021's accepted effects with the same predicates. No model call is made.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from grimoire.core.identity import InstanceRef, RelationRef, paired_instances, validate_segment
from grimoire.core.knowledge import Moment
from grimoire.graph import Relation, UnsupportedRelation
from grimoire.graph.codec import RelationCodecError, relation_from_payload
from grimoire.graphstore import InvalidRelation, RelationChange, RelationLibrary
from grimoire.party import MEMBERSHIP, PREDICATE_FOR, PartyRefused, PartyState, SceneCast, stage
from grimoire.library import LibraryPathError
from grimoire.store import LibraryStore, LoadProblem, RecordNotFound, StaleWrite, StoreError
from grimoire.store.layout import address_parts

_ANY = "any"


def _refuse(reason: str, message: str, *members: InstanceRef) -> PartyRefused:
    return PartyRefused(message, reason, tuple(members))


@dataclass(frozen=True)
class _Files:
    """One read of a campaign's relation files, under the store lock."""

    relations: list[Relation]
    unreadable: bool
    #: every membership relation file's etag (an unsupported or retracted one too): the commit pin
    pins: Mapping[RelationRef, str]
    problems: tuple[LoadProblem, ...]


def _read(store: LibraryStore, campaign_id: str) -> _Files:
    found = store.campaign_relations(campaign_id)
    relations: list[Relation] = []
    pins: dict[RelationRef, str] = {}
    unreadable = bool(found.problems)
    for ref, stored in found.records.items():
        try:
            decoded = relation_from_payload(ref, stored.record.schema, stored.record.payload)  # type: ignore[arg-type]
        except RelationCodecError:
            unreadable = True
            continue
        if isinstance(decoded, UnsupportedRelation):
            continue  # a newer build's predicate: never ours to interpret
        relations.append(decoded)
        if decoded.predicate in MEMBERSHIP:
            pins[ref] = stored.etag
    return _Files(relations, unreadable, pins, found.problems)


@dataclass(frozen=True)
class PartyView:
    """One campaign's membership at one sequence, its instance files, and the store's relation problems."""

    state: PartyState
    instances: tuple[InstanceRef, ...]
    problems: tuple[LoadProblem, ...]


@dataclass(frozen=True)
class PartyCommit:
    etags: Mapping[str, str]
    ended: RelationRef | None
    added: RelationRef | None


@dataclass
class PartyChange:
    """One move, not yet committed: ``preview`` for a human, then ``commit``.

    ``before`` is the member's membership as planned (``(party, status)`` or ``None``), ``after`` the target.
    """

    library: PartyLibrary
    campaign_id: str
    member: InstanceRef
    before: tuple[InstanceRef, str] | None
    after: tuple[InstanceRef, str] | None
    at: Moment
    relation_changes: tuple[RelationChange, ...]
    pins: Mapping[RelationRef, str]
    _committed: bool = field(default=False, repr=False)

    def _transaction(self):  # type: ignore[no-untyped-def]
        transaction = self.library.store.transaction()
        for change in self.relation_changes:
            for record, expected in change.puts:
                transaction.put(record, expected)
        return transaction

    def preview(self) -> dict[str, bytes]:
        """The exact bytes every relation file will hold, keyed by logical path."""
        return self._transaction().preview()

    def stale(self) -> list[tuple[str, str]]:
        """Why this move can no longer be committed as previewed; empty when it still can.

        Any membership relation file of the campaign added, changed or removed since the plan, a relation
        file that has become unreadable, and each folded 017 change's own check (its endpoint files, CAS and
        duplicates).
        """
        store = self.library.store
        with store.lock:
            files = _read(store, self.campaign_id)
            stale: list[tuple[str, str]] = []
            if files.unreadable:
                stale.append((f"campaigns/{self.campaign_id}/relations", "a relation file cannot be read"))
            if dict(files.pins) != dict(self.pins):
                stale.append((f"campaigns/{self.campaign_id}/relations", "memberships changed"))
            for change in self.relation_changes:
                stale += change.stale(files.relations)
                for record, expected in change.puts:  # a new file's id taken since the plan (commit says so too)
                    if expected is None and not _absent(store, record.address):
                        stale.append(("/".join(address_parts(record.address)), "exists"))
            return list(dict.fromkeys(stale))

    def commit(self, approvals: Mapping[str, str] | None = None) -> PartyCommit:
        store = self.library.store
        with store.lock:  # nothing can write between these checks and the commit
            if self._committed:
                raise StoreError("this change was already committed")
            stale = self.stale()
            if stale:
                raise StaleWrite(stale)
            result = self._transaction().commit(self.library.handle, approvals)
            self._committed = True
        ended = added = None
        for change in self.relation_changes:
            if change.new is None:
                ended = change.relation
            else:
                added = change.relation
        return PartyCommit(result.etags, ended, added)


class PartyLibrary:
    """Durable party membership over one open, writable ``LibraryStore``."""

    def __init__(self, store: LibraryStore, handle: object, *, clock: Callable[[], str],
                 new_id: Callable[[str], str], owner: str | None = None) -> None:
        self.store = store
        self.handle = handle
        self.relations = RelationLibrary(store, handle, clock=clock, new_id=new_id, owner=owner)

    # -- reading ------------------------------------------------------------------------------

    def view(self, campaign_id: str, *, as_of: int) -> PartyView:
        """Membership at ``as_of``, rebuilt from canonical files under one lock; nothing is cached."""
        try:
            validate_segment(campaign_id, "campaign_id")
        except ValueError as error:
            raise _refuse("invalid_field", str(error)) from None
        with self.store.lock:
            files = _read(self.store, campaign_id)
            instances = self.store.campaign_instances(campaign_id)
        state = PartyState.build(campaign_id, files.relations, as_of=as_of, unreadable=files.unreadable)
        return PartyView(state, instances, files.problems)

    def history(self, member: InstanceRef) -> tuple[Relation, ...]:
        """Every membership relation ``member`` is the subject of, ended and retracted ones included, oldest first.

        This is the member's membership history; nothing in it is ever deleted. Refused with
        ``relations_unreadable`` when a relation file cannot be read, since one of its memberships may be in it.
        """
        _instance(member, "member")
        with self.store.lock:
            files = _read(self.store, member.campaign_id)
        if files.unreadable:
            raise _refuse("relations_unreadable", "a relation file cannot be read, so the history may be partial")
        found = [r for r in files.relations if r.predicate in MEMBERSHIP and r.subject == member]
        return tuple(sorted(found, key=lambda r: (r.since.sequence, r.ref.relation_id)))

    def stage(self, campaign_id: str, scene_id: str, present: Iterable[InstanceRef], *, as_of: int) -> SceneCast:
        """``grimoire.party.stage`` over the campaign as it is now. The cast is a value; nothing is written."""
        view = self.view(campaign_id, as_of=as_of)
        return stage(view.state, scene_id, present, instances=view.instances)

    # -- moves ----------------------------------------------------------------------------------

    def join(self, party: InstanceRef, member: InstanceRef, *, at: Moment, provenance: str) -> PartyChange:
        """A member of no party joins ``party``'s active party."""
        return self._plan(member, (party, "party"), at, provenance, None)

    def store_in_reserve(self, member: InstanceRef, *, at: Moment, provenance: str) -> PartyChange:
        """An active member is stored in its own party's reserve (boxed)."""
        return self._plan(member, (None, "reserve"), at, provenance, "party")

    def withdraw(self, member: InstanceRef, *, at: Moment, provenance: str) -> PartyChange:
        """A reserved member rejoins its own party's active members."""
        return self._plan(member, (None, "party"), at, provenance, "reserve")

    def leave(self, member: InstanceRef, *, at: Moment, provenance: str) -> PartyChange:
        """An active member leaves its party; it stays in the campaign, a member of no party."""
        return self._plan(member, None, at, provenance, "party")

    def release(self, member: InstanceRef, *, at: Moment, provenance: str) -> PartyChange:
        """An active or reserved member is let go. Its instance and everything it remembers are kept."""
        return self._plan(member, None, at, provenance, _ANY)

    def move(self, member: InstanceRef, party: InstanceRef, status: str, *, at: Moment,
             provenance: str) -> PartyChange:
        """The general move: from anywhere (or nowhere) to ``party`` with ``status`` (``party`` or ``reserve``)."""
        if status not in PREDICATE_FOR:
            raise _refuse("invalid_field", f"status must be one of {sorted(PREDICATE_FOR)}")
        return self._plan(member, (party, status), at, provenance, None, any_source=True)

    # -- planning -------------------------------------------------------------------------------

    def _plan(self, member: InstanceRef, target: tuple[InstanceRef | None, str] | None, at: Moment,
              provenance: str, source: str | None, *, any_source: bool = False) -> PartyChange:
        """``source``: the membership status the member must have now (``None``: no membership; ``any``: some).
        A target party of ``None`` is the member's own current party, read under the same lock as the move."""
        _instance(member, "member")
        if target is not None and target[0] is not None:
            _instance(target[0], "party")
        _moment(at)
        if type(provenance) is not str or not provenance.strip():
            raise _refuse("invalid_field", "provenance names who records the move")
        campaign = member.campaign_id
        if target is not None and target[0] is not None and target[0].campaign_id != campaign:
            raise _refuse("cross_campaign", f"{target[0]} is not in campaign {campaign}", target[0])
        if target is not None and target[0] == member:
            raise _refuse("self_membership", "an individual cannot be in its own party", member)
        store = self.store
        with store.lock:  # the decision and the folded 017 changes are made over one snapshot
            if store.campaign_presence(campaign) != "present":
                raise _refuse("missing_campaign", f"campaign {campaign} does not exist")
            instances = set(store.campaign_instances(campaign))
            if member not in instances:
                raise _refuse("missing_member", f"{member} has no instance file", member)
            if member in paired_instances(instances):
                raise _refuse("instance_id_conflict", "an id also used by another kind is no one individual", member)
            files = _read(store, campaign)
            if files.unreadable:
                raise _refuse("relations_unreadable", "a relation file cannot be read, so membership is unknown")
            live = [r for r in files.relations if r.predicate in MEMBERSHIP and r.retraction is None]
            # Membership history is appended in campaign order: a move checked at ``at`` (for a cycle, a stored
            # anchor) stays valid afterwards only if no membership of the campaign is recorded after ``at``.
            later = sorted((r for r in live if r.since.sequence > at.sequence
                            or (r.ending is not None and r.ending.until.sequence > at.sequence)),
                           key=lambda r: r.ref.relation_id)
            if later:
                raise _refuse("out_of_order", f"a membership is already recorded after sequence {at.sequence} "
                                              f"({later[0].ref}); record the move at a later sequence",
                              *dict.fromkeys(r.subject for r in later))
            mine = [r for r in live if r.subject == member]
            holding = [r for r in mine if r.holds_at(at.sequence)]
            if len(holding) > 1:
                raise _refuse("conflicting_membership", f"{member} holds more than one membership", member)
            current = holding[0] if holding else None
            before = None if current is None else (current.object, MEMBERSHIP[current.predicate])
            self._check_source(member, before, source, any_source)
            if target is not None and target[0] is None:
                assert before is not None  # _check_source required a membership
                target = (before[0], target[1])
            if target is not None:
                if target[0] not in instances:
                    raise _refuse("missing_party", f"{target[0]} has no instance file", target[0])
                if target[0] in paired_instances(instances):
                    raise _refuse("instance_id_conflict", "an id also used by another kind is no one individual",
                                  target[0])
            if target is not None and before == target:
                raise _refuse("already_there", f"{member} is already there", member)
            if current is not None and current.since.sequence >= at.sequence:
                raise _refuse("same_moment", f"{member}'s current membership began at sequence {at.sequence}; "
                                             "record the move at a later sequence", member)
            if target is not None:
                state = PartyState.build(campaign, files.relations, as_of=at.sequence)
                if state.uncertain(target[0]):
                    raise _refuse("conflicting_membership", f"{target[0]}'s membership is ambiguous", target[0])
                if member in state.anchors_above(target[0]):
                    raise _refuse("party_cycle", f"{target[0]} is itself in {member}'s party", member, target[0])
                if target[1] == "party" and state.reserved(target[0]):
                    raise _refuse("party_in_reserve", f"{target[0]} is stored, so its party cannot travel",
                                  target[0])
            changes: list[RelationChange] = []
            try:
                if current is not None:
                    etag = store.read(current.ref).etag
                    changes.append(self.relations.end(current.ref, until=at, provenance=provenance, expected=etag))
                if target is not None:
                    changes.append(self.relations.add(campaign, PREDICATE_FOR[target[1]], member, target[0],
                                                      since=at, provenance=provenance, relations=files.relations))
            except InvalidRelation as error:
                raise _refuse(error.reason, str(error), member) from None
        return PartyChange(self, campaign, member, before, target, at, tuple(changes), files.pins)  # type: ignore[arg-type]

    @staticmethod
    def _check_source(member: InstanceRef, before: tuple[InstanceRef, str] | None, source: str | None,
                      any_source: bool) -> None:
        if any_source:
            return
        if source is None and before is not None:
            raise _refuse("already_member", f"{member} is already in a party or reserve; move it instead", member)
        if source == _ANY and before is None:
            raise _refuse("not_a_member", f"{member} is in no party or reserve", member)
        if source in PREDICATE_FOR and (before is None or before[1] != source):
            raise _refuse("not_in_party" if source == "party" else "not_in_reserve",
                          f"{member} is not {'an active member' if source == 'party' else 'in a reserve'}", member)


def _absent(store: LibraryStore, address: object) -> bool:
    try:
        store.read(address)  # type: ignore[arg-type]
    except RecordNotFound:
        return True
    except (StoreError, LibraryPathError):
        return False  # something is there, even if unreadable
    return False


def _instance(value: object, label: str) -> None:
    if type(value) is not InstanceRef:
        raise _refuse("invalid_field", f"the {label} must be an InstanceRef")


def _moment(value: object) -> None:
    if type(value) is not Moment:
        raise _refuse("invalid_field", "at must be a Moment")
