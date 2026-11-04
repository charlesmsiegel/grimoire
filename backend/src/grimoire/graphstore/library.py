"""Store-backed typed relations (Sprint 017): add, end and retract campaign relations durably.

``RelationLibrary`` is trusted application code and is never handed to a worker. Every mutation
returns a ``RelationChange``: one store transaction a human can ``preview`` and approve before
``commit``. The store applies CAS, authorization, link/V1 guards, journal and recovery.

Writes read one campaign's relation and instance listings, never the whole library. An endpoint
is an existing instance file named exactly; nothing is ever rebound by name. Relations are
editorial world facts: no player-consent gate applies in this slice (play-derived changes belong
to 021's accepted effects). No model call is made, and nothing writes events, evidence or beliefs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from grimoire.core.identity import InstanceRef, RelationRef, validate_segment
from grimoire.core.knowledge import Moment
from grimoire.graph import (
    PREDICATES, Ending, GraphError, GraphIndex, Relation, Reopening, Retraction, UnsupportedRelation,
)
from grimoire.graph.codec import RELATION_SCHEMA, RelationCodecError, relation_from_payload, relation_to_payload
from grimoire.graph.index import require_director
from grimoire.library import LibraryPathError
from grimoire.store import (
    LibraryStore, LoadProblem, RecordNotFound, ScopeRecord, StaleWrite, StoreError, StoredScopeRecord,
)
from grimoire.store.layout import address_parts, classify

_ALLOCATION_ATTEMPTS = 16


class InvalidRelation(ValueError):
    """A relation change that cannot be made; nothing was written. ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


@dataclass(frozen=True)
class RelationCommit:
    etags: Mapping[str, str]
    relation: RelationRef


@dataclass(frozen=True)
class CampaignGraph:
    """Director-only: the campaign's index, each readable relation file's etag, and store problems."""

    index: GraphIndex
    etags: Mapping[RelationRef, str]
    problems: tuple[LoadProblem, ...]


def _path(ref: RelationRef | InstanceRef) -> str:
    return "/".join(address_parts(ref))


def _refused(error: GraphError) -> InvalidRelation:
    return InvalidRelation(f"the relation change is refused: {error.reason}", error.reason)


def _decode(ref: RelationRef, stored: StoredScopeRecord) -> Relation | UnsupportedRelation:
    return relation_from_payload(ref, stored.record.schema, stored.record.payload)


def _decoded(store: LibraryStore, campaign_id: str) -> list[Relation | UnsupportedRelation]:
    found = []
    for ref, stored in store.campaign_relations(campaign_id).records.items():
        try:
            found.append(_decode(ref, stored))
        except RelationCodecError:
            continue  # undecodable files never block a write; graph() reports them
    return found


def relation_endpoint_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    """Instance ids named as endpoints by the campaign's decodable and unsupported relation files.

    Every instance allocator must treat these as taken, so a hand-deleted endpoint's id is never
    given to a different individual that would silently inherit its relations. Ids named only by
    an undecodable file are not protected (a documented limit).
    """
    taken: set[str] = set()
    for found in _decoded(store, campaign_id):
        ends = found.endpoints() if isinstance(found, Relation) else found.endpoints
        taken |= {ref.instance_id for ref in ends}
    return frozenset(taken)


@dataclass
class RelationChange:
    """One store transaction, not yet committed: ``preview`` for a human, then ``commit``."""

    library: RelationLibrary
    puts: tuple[tuple[ScopeRecord, str | None], ...]
    relation: RelationRef
    new: Relation | None = None  # set for an add; its endpoints and duplicates are re-checked at commit
    endpoint_etags: Mapping[InstanceRef, str] = field(default_factory=dict)  # the instance files it was checked against
    reopen: Relation | None = None  # set for a reopen; its overlap is re-checked at commit
    _committed: bool = field(default=False, repr=False)

    def _transaction(self):  # type: ignore[no-untyped-def]
        transaction = self.library.store.transaction()
        for record, expected in self.puts:
            transaction.put(record, expected)
        return transaction

    def preview(self) -> dict[str, bytes]:
        """The exact bytes the relation file will hold, keyed by logical path."""
        return self._transaction().preview()

    def stale(self, relations: list | None = None) -> list[tuple[str, str]]:
        """Why this change can no longer be committed as previewed; empty when it still can.

        For an add, exactly the check ``commit`` makes. For a rewrite (end, retract, reopen), whether
        the relation file still has the etag the change expects (``absent``/``changed``, the store's
        own CAS words), and for a reopen also whether the reopened interval now overlaps a duplicate.
        Callers folding this change into a larger transaction (021) call it under the store lock,
        and may pass ``relations`` (the campaign's decoded relations, read under that lock) to check many
        changes against one read.
        """
        store = self.library.store
        with store.lock:
            if self.new is not None:
                return self.library._stale_add(self.new, self.endpoint_etags, relations)
            stale = []
            for record, expected in self.puts:
                try:
                    current = store.read(record.address).etag
                except RecordNotFound:
                    current = None
                except (StoreError, LibraryPathError):
                    current = ""
                if current != expected:
                    stale.append((_path(record.address), "absent" if current is None else "changed"))
            if self.reopen is not None:
                stale += self.library._stale_reopen(self.reopen, relations)
            return stale

    def commit(self, approvals: Mapping[str, str] | None = None) -> RelationCommit:
        store = self.library.store
        with store.lock:  # nothing can write between these checks and the commit
            if self._committed:
                raise StoreError("this change was already committed")
            if self.new is not None:
                stale = self.library._stale_add(self.new, self.endpoint_etags)
                if stale:
                    raise StaleWrite(stale)
            if self.reopen is not None:
                stale = self.library._stale_reopen(self.reopen)
                if stale:
                    raise StaleWrite(stale)
            result = self._transaction().commit(self.library.handle, approvals)
            self._committed = True
        return RelationCommit(result.etags, self.relation)


class RelationLibrary:
    """Durable campaign relations over one open, writable ``LibraryStore``."""

    def __init__(self, store: LibraryStore, handle: object, *, clock: Callable[[], str],
                 new_id: Callable[[str], str], owner: str | None = None) -> None:
        self.store = store
        self.handle = handle
        self._clock = clock
        self._new_id = new_id
        self._owner = owner

    # -- reading ------------------------------------------------------------------------------

    def read(self, ref: RelationRef) -> tuple[Relation | UnsupportedRelation, str]:
        """The relation and its etag. Store errors propagate; an undecodable payload is ``InvalidRelation``."""
        found = self.store.read(ref)
        try:
            return _decode(ref, found), found.etag  # type: ignore[arg-type]
        except RelationCodecError as error:
            raise InvalidRelation(f"{ref} cannot be decoded: {error.reason}", "undecodable_relation") from None

    def graph(self, campaign_id: str, *, requested_by: str) -> CampaignGraph:
        """Director-only. One campaign's graph, rebuilt from canonical files under one lock; nothing is cached."""
        require_director(requested_by)
        validate_segment(campaign_id, "campaign_id")
        with self.store.lock:  # relations and instances form one consistent snapshot
            found = self.store.campaign_relations(campaign_id)
            instances = self.store.campaign_instances(campaign_id)
        relations, unsupported, undecodable, etags = [], [], [], {}
        for ref, stored in found.records.items():
            etags[ref] = stored.etag
            try:
                decoded = _decode(ref, stored)
            except RelationCodecError as error:
                undecodable.append((ref, error.reason))
                continue
            (relations if isinstance(decoded, Relation) else unsupported).append(decoded)
        for problem in found.problems:  # files the store itself could not read
            hit = classify(tuple(problem.path.split("/")))
            if hit is not None and hit[0] == "relation" and hit[1] not in found.records:
                undecodable.append((hit[1], problem.code))
        index = GraphIndex.build(campaign_id, relations, instances, unsupported=unsupported,
                                 undecodable=undecodable)
        return CampaignGraph(index, etags, found.problems)

    # -- writing ------------------------------------------------------------------------------

    def add(self, campaign_id: str, predicate: str, subject: InstanceRef, object: InstanceRef, *, since: Moment,
            provenance: str, until: Moment | None = None, relations: list | None = None) -> RelationChange:
        """A new relation; with ``until``, a past or bounded stint written already ended."""
        validate_segment(campaign_id, "campaign_id")
        if self.store.campaign_presence(campaign_id) != "present":  # a look-alike folder is not the campaign
            raise InvalidRelation(f"campaign {campaign_id} does not exist", "missing_campaign")
        if type(predicate) is not str or predicate not in PREDICATES:
            raise InvalidRelation("unknown predicate", "unknown_predicate")
        for endpoint in (subject, object):
            if type(endpoint) is not InstanceRef:
                raise InvalidRelation("an endpoint must be an InstanceRef", "invalid_field")
            if endpoint.campaign_id != campaign_id:
                raise InvalidRelation(f"{endpoint} is not in campaign {campaign_id}", "campaign_mismatch")
        if subject == object:
            raise InvalidRelation("a relation needs two different individuals", "self_relation")
        existing = set(self.store.campaign_instances(campaign_id))
        for endpoint in (subject, object):
            if endpoint not in existing:
                raise InvalidRelation(f"{endpoint} has no instance file", "missing_endpoint")
        etags = {endpoint: self._instance_etag(endpoint) for endpoint in (subject, object)}
        at = self._clock()
        ref = self._allocate(campaign_id)
        try:
            ending = None if until is None else Ending(until, provenance, at)
            relation = Relation(ref, predicate, subject, object, since, provenance, at, ending)
        except GraphError as error:
            raise _refused(error) from None
        if any(isinstance(other, Relation) and other.overlaps(relation)
               for other in (_decoded(self.store, campaign_id) if relations is None else relations)):
            raise InvalidRelation("the same relation is already recorded for an overlapping time",
                                  "duplicate_relation")
        record = ScopeRecord(ref, RELATION_SCHEMA, relation_to_payload(relation), owner=self._owner)
        return RelationChange(self, ((record, None),), ref, relation, etags)

    def end(self, ref: RelationRef, *, until: Moment, provenance: str, expected: str) -> RelationChange:
        """The relation stopped holding at ``until``. Works when an endpoint is missing (repair)."""
        current, stored = self._current(ref, expected)
        try:
            changed = current.ended(Ending(until, provenance, self._clock()))
        except GraphError as error:
            raise _refused(error) from None
        return self._rewrite(changed, stored, expected)

    def retract(self, ref: RelationRef, *, provenance: str, expected: str) -> RelationChange:
        """The record was wrong: tombstone it. Nothing is deleted. Works when an endpoint is missing."""
        current, stored = self._current(ref, expected)
        try:
            changed = current.retracted(Retraction(provenance, self._clock()))
        except GraphError as error:
            raise _refused(error) from None
        return self._rewrite(changed, stored, expected)

    def reopen(self, ref: RelationRef, *, provenance: str, expected: str, relations: list | None = None
               ) -> RelationChange:
        """Cancel the relation's ending (021's undo): it holds again from ``since``; the ending is kept.

        Refused when it has no ending (``not_ended``), is retracted, or would then overlap another
        record of the same fact (``duplicate_relation``). Works when an endpoint is missing.
        """
        current, stored = self._current(ref, expected)
        if current.ending is None:
            raise InvalidRelation(f"{ref} has no ending to reopen", "not_ended")
        try:
            changed = current.reopened(Reopening(current.ending, provenance, self._clock()))
        except GraphError as error:
            raise _refused(error) from None
        if self._stale_reopen(changed, relations):
            raise InvalidRelation("the same relation is already recorded for an overlapping time",
                                  "duplicate_relation")
        change = self._rewrite(changed, stored, expected)
        change.reopen = changed
        return change

    # -- helpers -------------------------------------------------------------------------------

    def _allocate(self, campaign_id: str) -> RelationRef:
        for _ in range(_ALLOCATION_ATTEMPTS):
            value = self._new_id("r")
            validate_segment(value, "generated id")
            ref = RelationRef(campaign_id, value)
            try:
                self.store.read(ref)
            except RecordNotFound:  # exact presence only: a look-alike fails the commit with NameCollision
                return ref
            except (StoreError, LibraryPathError):
                continue  # a file (or a link) is there, even if unreadable
        raise StoreError("could not allocate an unused relation id")

    def _current(self, ref: RelationRef, expected: str) -> tuple[Relation, StoredScopeRecord]:
        found = self.store.read(ref)
        if found.etag != expected:
            raise StaleWrite([(_path(ref), "changed")])
        try:
            decoded = _decode(ref, found)  # type: ignore[arg-type]
        except RelationCodecError:
            raise InvalidRelation(f"{ref} cannot be decoded", "undecodable_relation") from None
        if isinstance(decoded, UnsupportedRelation):
            raise InvalidRelation(f"{ref} has a predicate this version does not know", "unsupported_relation")
        return decoded, found  # type: ignore[return-value]

    def _rewrite(self, relation: Relation, stored: StoredScopeRecord, expected: str) -> RelationChange:
        """Keep the stored envelope's attribution and unknown fields, even none."""
        record = ScopeRecord(relation.ref, RELATION_SCHEMA, relation_to_payload(relation), owner=stored.record.owner,
                             extra=stored.record.extra)
        return RelationChange(self, ((record, expected),), relation.ref)

    def _instance_etag(self, ref: InstanceRef) -> str | None:
        try:
            return self.store.read(ref).etag
        except (StoreError, LibraryPathError):
            return None  # an unreadable file still names the endpoint; its bytes are pinned as "unreadable"

    def _stale_reopen(self, relation: Relation, relations: list | None = None) -> list[tuple[str, str]]:
        campaign = relation.ref.campaign_id
        if any(isinstance(other, Relation) and other.ref != relation.ref and other.overlaps(relation)
               for other in (_decoded(self.store, campaign) if relations is None else relations)):
            return [(_path(relation.ref), "duplicate recorded")]
        return []

    def _stale_add(self, relation: Relation, etags: Mapping[InstanceRef, str | None],
                   relations: list | None = None) -> list[tuple[str, str]]:
        """Why an add previewed earlier may no longer be written (checked under the store lock).

        An endpoint file that was replaced since the preview may hold a different individual
        under the same id, so its bytes must be the ones checked then.
        """
        campaign = relation.ref.campaign_id
        existing = set(self.store.campaign_instances(campaign))
        stale = [(_path(end), "endpoint missing") for end in (relation.subject, relation.object)
                 if end not in existing]
        stale += [(_path(end), "endpoint changed") for end in (relation.subject, relation.object)
                  if end in existing and end in etags and self._instance_etag(end) != etags[end]]
        if any(isinstance(other, Relation) and other.ref != relation.ref and other.overlaps(relation)
               for other in (_decoded(self.store, campaign) if relations is None else relations)):
            stale.append((_path(relation.ref), "duplicate recorded"))
        return stale
