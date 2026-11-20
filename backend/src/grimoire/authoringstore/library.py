"""Review world authoring proposals (087) against a world and write the accepted subset.

``AuthoringLibrary(store, handle)`` is trusted application code, like the other store services: it
is never handed to a worker. It reads a world for a proposer (``context``), checks a proposal
against the world as it is (``review``: namesakes and links the world can no longer carry), and
turns a human's decision into one World Change Set (043): a new entity revision for each accepted
candidate and a new revision of the world's topology (059) carrying the roads and containment
between them. Nothing is written until ``apply``, which commits exactly the reviewed bytes, or
refuses and writes nothing. A rejected candidate, a brainstorm and a link to either are never
written, and the proposal itself is never persisted. See docs/AUTHORING.md.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from grimoire.authoring import (
    AuthoringContext, AuthoringError, Candidate, Decision, Duplicate, LinkProblem, Proposal, Selection,
    WorldRecord, name_key, select,
)
from grimoire.changesets import ChangeSet, ChangeSetError, RecordChange
from grimoire.changesetstore import AppliedChangeSet, ChangeSetLibrary, ChangeSetReview
from grimoire.core.entity import EntityDefinition
from grimoire.core.entity_codec import ENTITY_SCHEMA, CodecError, definition_from_payload, definition_to_payload
from grimoire.core.facets import FacetSet, Spatial
from grimoire.core.identity import SEGMENT_MAX, SourceRef, validate_segment
from grimoire.store import LibraryStore, LibraryView
from grimoire.topology import Path, Place, Topology, TopologyError
from grimoire.topology.codec import decode_topology, encode_topology

#: The most existing records offered to a proposer; the rest are counted as ``truncated``.
MAX_CONTEXT = 200
TOPOLOGY = "topology"
#: The entity id of a topology the first accepted link creates, when the world has none.
NEW_TOPOLOGY_ID = "map"
_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ProposalReview:
    """A proposal judged against the world: what a reviewer is shown before deciding.

    ``duplicates`` are candidates whose name matches an existing record of the same kind, or
    another candidate; ``problems`` are links whose existing endpoint is missing, has moved or is not a
    place. ``basis`` is the source manifest etag the judgement was made against."""

    proposal: Proposal
    duplicates: tuple[Duplicate, ...]
    problems: tuple[LinkProblem, ...]
    basis: str


@dataclass(frozen=True)
class AcceptanceDraft:
    """A decision turned into the exact change set it would apply.

    ``records`` maps each accepted candidate's key to the pinned revision it becomes; ``topology`` is
    the topology revision written for the kept links (``None`` when no link is kept). ``changes`` is
    043's review: every file's bytes and diff, the conflicts that stop it and the authorization each
    file needs. Show it, and ``selection``'s dropped links and surfaced duplicates, before applying."""

    review: ProposalReview
    decision: Decision
    selection: Selection
    records: Mapping[str, SourceRef]
    topology: SourceRef | None
    changes: ChangeSetReview

    @property
    def applicable(self) -> bool:
        return self.changes.applicable


@dataclass(frozen=True)
class AcceptedRecords:
    """What ``apply`` wrote: the accepted candidates' revisions and the topology revision."""

    proposal_id: str
    records: Mapping[str, SourceRef]
    topology: SourceRef | None
    changeset: AppliedChangeSet


def _slug(name: str, fallback: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").casefold()
    slug = _SLUG.sub("-", ascii_name).strip("-")[:64].strip("-")
    return slug or fallback


def _fresh(base: str, taken: set[str]) -> str:
    """``base``, or ``base-2``, ``base-3``...: the first that no entity of the source uses, in any case."""
    entity_id, number = base, 2
    while entity_id.casefold() in taken:
        suffix = f"-{number}"
        entity_id = base[:SEGMENT_MAX - len(suffix)] + suffix
        number += 1
    validate_segment(entity_id, "entity_id")
    taken.add(entity_id.casefold())
    return entity_id


def _is_place(stored: object, ref: SourceRef) -> bool:
    """An existing record a road or containment may join: a ``location``, or an entity with ``spatial``."""
    if ref.kind == "location":
        return True
    envelope = getattr(stored, "envelope", None)
    if envelope is None or envelope.extra.get("schema") != ENTITY_SCHEMA:
        return False
    try:
        return definition_from_payload(ref, ENTITY_SCHEMA, envelope.payload).traits.has(Spatial)
    except (CodecError, ValueError):
        return False


class AuthoringLibrary:
    """Offer a world to a proposer, review proposals against it and apply accepted subsets."""

    def __init__(self, store: LibraryStore, handle: object) -> None:
        self.store = store
        self.changes = ChangeSetLibrary(store, handle)

    # -- reading --------------------------------------------------------------------------------
    def _world(self, source: str) -> tuple[LibraryView, str, dict[tuple[str, str], object]]:
        """The loaded view, the manifest etag and each entity's head definition, keyed by (kind, id)."""
        view = self.store.load()
        if source in view.excluded:
            raise AuthoringError("excluded_source")
        stored = view.sources.get(source)
        if stored is None:
            raise AuthoringError("missing_source")
        heads: dict[tuple[str, str], object] = {}
        for definition in view.index.definitions():
            ref = definition.ref
            if ref.source != source or (ref.kind, ref.entity_id) in heads:
                continue
            try:
                heads[(ref.kind, ref.entity_id)] = view.index.resolve(SourceRef(source, ref.kind, ref.entity_id))
            except (ValueError, LookupError):  # no declared head among several revisions: not offered
                continue
        return view, stored.etag, heads

    def context(self, source: str, *, limit: int = MAX_CONTEXT) -> AuthoringContext:
        """The world's title and up to ``limit`` existing records (head revisions), for a proposer.

        Places and groups come first, then other kinds, each by name. Topology records are left out:
        a proposer links places, not maps."""
        if type(limit) is not int or not 0 <= limit <= MAX_CONTEXT:
            raise AuthoringError("invalid_bound")
        view, basis, heads = self._world(source)
        order = {"location": 0, "organization": 1}
        records = sorted(
            (WorldRecord(definition.ref, kind, definition.name)  # type: ignore[attr-defined]
             for (kind, _), definition in heads.items() if kind != TOPOLOGY and getattr(definition, "name", "")),
            key=lambda record: (order.get(record.kind, 2), record.kind, record.name.casefold(), str(record.ref)))
        return AuthoringContext(source, view.sources[source].manifest.title, tuple(records[:limit]),
                                len(records) > limit, basis)

    # -- review ---------------------------------------------------------------------------------
    def review(self, proposal: Proposal) -> ProposalReview:
        """Judge ``proposal`` against its world now. Writes nothing."""
        if type(proposal) is not Proposal:
            raise AuthoringError("invalid_proposal")
        view, basis, heads = self._world(proposal.request.source)
        duplicates, problems = self._judge(proposal, view, heads)
        return ProposalReview(proposal, duplicates, problems, basis)

    def _judge(self, proposal: Proposal, view: LibraryView, heads: Mapping[tuple[str, str], object]
               ) -> tuple[tuple[Duplicate, ...], tuple[LinkProblem, ...]]:
        existing: dict[tuple[str, tuple[str, ...]], list[object]] = {}
        for (kind, _), definition in heads.items():
            name = getattr(definition, "name", "")
            if name:
                existing.setdefault((kind, name_key(name)), []).append(definition)
        duplicates = []
        for candidate in proposal.candidates:
            key = (candidate.kind, name_key(candidate.name))
            slug = _slug(candidate.name, candidate.key)
            same = {str(d.ref): d for d in existing.get(key, ())}  # type: ignore[attr-defined]
            named = heads.get((candidate.kind, slug))  # its id would be taken by one of the same kind
            if named is not None:
                same.setdefault(str(named.ref), named)  # type: ignore[attr-defined]
            for _, definition in sorted(same.items()):
                duplicates.append(Duplicate(candidate.key, existing=definition.ref,  # type: ignore[attr-defined]
                                            existing_name=getattr(definition, "name", "")))
            for other in proposal.candidates:
                if other.key != candidate.key and other.kind == candidate.kind and (
                        name_key(other.name) == key[1] or _slug(other.name, other.key) == slug):
                    duplicates.append(Duplicate(candidate.key, other=other.key))
        problems = []
        for link in proposal.links:
            target = link.object
            if type(target) is not SourceRef:
                continue
            head = heads.get((target.kind, target.entity_id))
            if head is None or view.index.check(target) is not None:
                problems.append(LinkProblem(link.link_id, "missing_endpoint"))
            elif head.ref != target:  # type: ignore[attr-defined]
                problems.append(LinkProblem(link.link_id, "endpoint_moved"))
            elif not _is_place(head, target):
                problems.append(LinkProblem(link.link_id, "not_a_place"))
        return tuple(duplicates), tuple(problems)

    # -- decision -------------------------------------------------------------------------------
    def draft(self, review: ProposalReview, decision: Decision, *, topology: str | None = None) -> AcceptanceDraft:
        """Turn a human's decision into the exact change set it would apply. Writes nothing.

        The world is read again: if what the reviewer was shown (namesakes, broken links) no longer
        holds, the draft is refused as ``stale_review`` and the proposal must be reviewed again.
        ``topology`` names the topology entity to extend when the world has several."""
        if type(review) is not ProposalReview:
            raise AuthoringError("invalid_review")
        with self.store.lock:  # one read of the world builds the content and the guards that check it
            return self._draft(review, decision, topology)

    def _draft(self, review: ProposalReview, decision: Decision, topology: str | None) -> AcceptanceDraft:
        proposal = review.proposal
        source = proposal.request.source
        view, _, heads = self._world(source)
        if self._judge(proposal, view, heads) != (review.duplicates, review.problems):
            raise AuthoringError("stale_review")
        selection = select(proposal, decision, review.duplicates, review.problems)
        taken = {entity_id.casefold() for _, entity_id in self.store.source_entities(source)}
        taken |= self._mapped(source, heads)  # a map may name a place whose record is gone: never reuse it
        records: dict[str, SourceRef] = {}
        for candidate in selection.accepted:
            records[candidate.key] = SourceRef(source, candidate.kind, self._allocate(candidate, taken), "r1")
        evidence = (f"suggested by {proposal.origin} in proposal {proposal.proposal_id}",
                    f"accepted by {decision.decided_by}")
        changes: list[RecordChange] = []
        for candidate in selection.accepted:
            ref = records[candidate.key]
            changes.append(self.changes.create(source, ref.kind, ref.entity_id, self._payload(candidate, ref, proposal,
                                                                                         decision),
                                               name=candidate.name, evidence=evidence, schema=ENTITY_SCHEMA))
        topology_ref, preconditions = None, []
        if selection.links:
            change, topology_ref, pinned = self._topology(selection, records, proposal, heads, topology, taken,
                                                          evidence)
            changes.append(change)
            preconditions = [self.changes.pin(source, ref.kind, ref.entity_id) for ref in pinned]
        try:
            changeset = ChangeSet(f"authoring-{proposal.proposal_id}", source,
                                  f"Accept {len(selection.accepted)} suggested record(s)", decision.decided_by,
                                  tuple(changes), rationale=proposal.rationale, preconditions=tuple(preconditions))
        except ChangeSetError as error:
            raise AuthoringError(error.reason) from None
        return AcceptanceDraft(review, decision, selection, records, topology_ref, self.changes.review(changeset))

    @staticmethod
    def _mapped(source: str, heads: Mapping[tuple[str, str], object]) -> set[str]:
        """The folded ids of this source's places named by any of its readable topologies."""
        mapped: set[str] = set()
        for (kind, _), head in heads.items():
            if kind != TOPOLOGY:
                continue
            try:
                graph = decode_topology(head.ref, head.envelope.payload)  # type: ignore[attr-defined]
            except TopologyError:
                continue
            mapped |= {place.ref.entity_id.casefold() for place in graph.places if place.ref.source == source}
        return mapped

    @staticmethod
    def _allocate(candidate: Candidate, taken: set[str]) -> str:
        """A fresh entity id from the name: never one any kind of the source uses, in any case."""
        return _fresh(_slug(candidate.name, candidate.key), taken)

    @staticmethod
    def _payload(candidate: Candidate, ref: SourceRef, proposal: Proposal, decision: Decision) -> dict:
        """A ``grimoire.entity/0`` definition of the candidate, with where it came from."""
        traits = FacetSet((Spatial(),)) if candidate.is_place else FacetSet(())
        provenance = {"proposal": proposal.proposal_id, "request": proposal.request.request_id,
                      "suggested_by": proposal.origin, "accepted_by": decision.decided_by}
        return definition_to_payload(EntityDefinition(ref, candidate.name, candidate.role, traits,
                                                      candidate.description, {"authoring": provenance}))

    def _topology(self, selection: Selection, records: Mapping[str, SourceRef], proposal: Proposal,
                  heads: Mapping[tuple[str, str], object], named: str | None, taken: set[str],
                  evidence: tuple[str, ...]) -> tuple[RecordChange, SourceRef, tuple[SourceRef, ...]]:
        """The topology revision carrying the kept links, and the existing endpoints it relies on."""
        source = proposal.request.source
        maps = sorted(entity_id for kind, entity_id in heads if kind == TOPOLOGY)
        if named is not None:
            try:
                validate_segment(named, "topology")
            except ValueError:
                raise AuthoringError("invalid_topology") from None
            entity_id = named
        elif len(maps) > 1:
            raise AuthoringError("ambiguous_topology")
        else:
            entity_id = maps[0] if maps else None  # type: ignore[assignment]
        head = heads.get((TOPOLOGY, entity_id)) if entity_id is not None else None
        if head is not None:
            try:
                graph = decode_topology(head.ref, head.envelope.payload)  # type: ignore[attr-defined]
            except TopologyError:
                raise AuthoringError("unreadable_topology") from None
        else:
            if entity_id is None:
                entity_id = _fresh(NEW_TOPOLOGY_ID, taken)
            elif entity_id.casefold() in taken:  # a named id another kind uses: the store would refuse it
                raise AuthoringError("invalid_topology")
            graph = None
        places = {place.ref.entity_key: place for place in graph.places} if graph is not None else {}
        paths = list(graph.paths) if graph is not None else []
        path_ids = {path.path_id for path in paths}
        pinned: list[SourceRef] = []

        def place_of(endpoint: str | SourceRef) -> SourceRef:
            if type(endpoint) is not SourceRef:
                ref = records[endpoint]  # type: ignore[index]
                known = places.get(ref.entity_key)
                if known is None:
                    places[ref.entity_key] = Place(ref)
                elif known.ref != ref:  # a fresh id is never on a map: refuse rather than merge into it
                    raise AuthoringError("invalid_topology")
                return ref
            if endpoint not in pinned:  # the link relies on it as reviewed, on the map or not
                pinned.append(endpoint)
            known = places.get(endpoint.entity_key)
            if known is not None:  # the map already holds this place at its own pinned revision
                return known.ref
            places[endpoint.entity_key] = Place(endpoint)
            return endpoint

        for link in selection.links:
            subject, target = place_of(link.subject), place_of(link.object)
            if link.relation == "road":
                path_id = f"{proposal.proposal_id}-{link.link_id}"
                if path_id in path_ids:
                    raise AuthoringError("path_id_taken")
                path_ids.add(path_id)
                paths.append(Path(path_id, subject, target, bidirectional=True))
            else:
                places[subject.entity_key] = replace(places[subject.entity_key], parent=target)
        for ref in pinned:
            if ref.kind == TOPOLOGY and ref.entity_id == entity_id:
                raise AuthoringError("invalid_topology")
        provenance = graph.provenance if graph is not None else f"authoring proposal {proposal.proposal_id}"
        check_ref = SourceRef(source, TOPOLOGY, entity_id, "r1")  # type: ignore[arg-type]
        try:
            payload = encode_topology(Topology(check_ref, tuple(places.values()), tuple(paths), provenance))
        except TopologyError as error:
            raise AuthoringError(f"invalid_topology:{error.reason}") from None
        try:
            if head is None:
                change = self.changes.create(source, TOPOLOGY, entity_id, payload, name="Map",  # type: ignore[arg-type]
                                             evidence=evidence)
            else:
                change = self.changes.revise(source, TOPOLOGY, entity_id, payload, evidence=evidence)  # type: ignore[arg-type]
        except ChangeSetError as error:
            raise AuthoringError(error.reason) from None
        if head is not None and change.base != head.ref.revision:  # type: ignore[attr-defined]
            raise AuthoringError("stale_review")  # the map moved since it was read: build again
        return change, SourceRef(source, TOPOLOGY, entity_id, change.revision), tuple(pinned)  # type: ignore[arg-type]

    # -- approve, apply, reject -------------------------------------------------------------------
    def approve(self, draft: AcceptanceDraft, authority: object, *, approved_by: str,
                new_id: Callable[[], str]) -> dict[str, str]:
        """043's digest-bound approvals for the draft's files; call only from a human's direct action."""
        return self.changes.approve(draft.changes, authority, approved_by=approved_by, new_id=new_id)

    def apply(self, draft: AcceptanceDraft, approvals: Mapping[str, str] | None = None) -> AcceptedRecords:
        """Write exactly the drafted files, or raise 043's refusal and write nothing."""
        if type(draft) is not AcceptanceDraft:
            raise AuthoringError("invalid_draft")
        applied = self.changes.apply(draft.changes, approvals)
        return AcceptedRecords(draft.review.proposal.proposal_id, dict(draft.records), draft.topology, applied)

    def reject(self, draft: AcceptanceDraft, authority: object | None = None) -> None:
        """End the draft without writing; revokes approvals issued for it."""
        if type(draft) is not AcceptanceDraft:
            raise AuthoringError("invalid_draft")
        self.changes.reject(draft.changes, authority)
