"""The derived graph index: director-only, rebuildable, bounded (Sprint 017).

``GraphIndex.build`` is a pure function of a campaign's canonical relation records and the
set of instances that exist. It is a **director-only** type, like 012's ``DirectorKnowledge``:
every query takes ``requested_by`` and refuses anything but ``DIRECTOR``. That keeps call
sites explicit and auditable; it is not authorization. No actor view is ever computed from it.

Absence is open-world: ``recorded`` returning False means "not recorded", never "known
false". Damage is reported, never repaired: a relation whose endpoint does not exist is never
walked and never rebound by name; overlapping duplicates are all kept and reported.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from grimoire.core.authority import DIRECTOR, AuthorityError
from grimoire.core.identity import InstanceRef, RelationRef, validate_segment
from grimoire.core.json_value import thaw

from .codec import relation_to_payload
from .model import PREDICATES, GraphError, Relation, UnsupportedRelation

DIRECTIONS = ("out", "in", "any")
MAX_DEPTH = 8
MAX_STEPS = 2000


@dataclass(frozen=True, slots=True)
class GraphProblem:
    """``cited_by`` (``proposition:<id>``/``ruling:<id>``) and ``subject`` are set by ``check_subjects``."""

    code: str
    relation_ids: tuple[str, ...] = ()
    cited_by: str | None = None
    subject: str | None = None

    def sort_key(self) -> tuple:
        return (self.code, self.relation_ids, self.cited_by or "", self.subject or "")


@dataclass(frozen=True, slots=True)
class RelationEntry:
    """One relation file as the director sees it. ``relation`` is None when it could not be decoded."""

    ref: RelationRef
    relation: Relation | UnsupportedRelation | None
    retracted: bool = False
    ended: bool = False
    missing_endpoint: bool = False
    unsupported: bool = False
    undecodable: str | None = None  # the reason, when the file could not be decoded
    reopened: bool = False  # an ending was revoked at least once (021); see ``relation.reopenings``

    @property
    def traversable(self) -> bool:
        return isinstance(self.relation, Relation) and not self.retracted and not self.missing_endpoint


@dataclass(frozen=True, slots=True)
class Step:
    relation_id: str
    predicate: str
    source: InstanceRef
    target: InstanceRef
    depth: int
    forward: bool


@dataclass(frozen=True, slots=True)
class Traversal:
    start: InstanceRef
    as_of: int
    steps: tuple[Step, ...]
    reached: Mapping[InstanceRef, int]
    truncated: bool


def require_director(requested_by: object) -> None:
    if requested_by != DIRECTOR or type(requested_by) is not str:
        raise AuthorityError("the graph index is only for the director perspective")


def check_as_of(as_of: object) -> int:
    if type(as_of) is not int or as_of < 0:
        raise GraphError("invalid_as_of")
    return as_of


def _check_direction(direction: object) -> str:
    if direction not in DIRECTIONS or type(direction) is not str:
        raise GraphError("invalid_direction")
    return direction


def _check_predicates(predicates: object) -> frozenset[str] | None:
    if predicates is None:
        return None
    if isinstance(predicates, (str, bytes)) or not isinstance(predicates, Iterable):
        raise GraphError("invalid_predicates")
    names = tuple(predicates)
    if not names or not all(type(name) is str for name in names):
        raise GraphError("invalid_predicates")
    if not set(names) <= set(PREDICATES):
        raise GraphError("unknown_predicate")
    return frozenset(names)


def _check_bound(value: object, high: int) -> int:
    if type(value) is not int or not 1 <= value <= high:
        raise GraphError("invalid_bound")
    return value


def _incidence(relation: Relation, node: InstanceRef, direction: str) -> tuple[InstanceRef, bool] | None:
    """(the other endpoint, forward) if ``relation`` may be walked from ``node`` in ``direction``."""
    if relation.symmetric:
        if node in relation.endpoints():
            return (relation.object if relation.subject == node else relation.subject), True
        return None
    if relation.subject == node and direction in ("out", "any"):
        return relation.object, True
    if relation.object == node and direction in ("in", "any"):
        return relation.subject, False
    return None


class GraphIndex:
    """One campaign's typed graph; build it with ``GraphIndex.build``."""

    __slots__ = ("campaign_id", "instances", "problems", "_entries", "_walkable", "_incident")

    def __init__(self, campaign_id: str, instances: frozenset[InstanceRef], entries: Mapping[str, RelationEntry],
                 problems: tuple[GraphProblem, ...]) -> None:
        self.campaign_id = campaign_id
        self.instances = instances
        self.problems = problems
        self._entries = MappingProxyType(dict(entries))
        walkable: dict[InstanceRef, list[Relation]] = {}
        incident: dict[InstanceRef, list[RelationEntry]] = {}
        for entry in self._entries.values():
            relation = entry.relation
            if isinstance(relation, Relation):
                ends: Iterable[InstanceRef] = relation.endpoints()
            elif isinstance(relation, UnsupportedRelation):
                ends = set(relation.endpoints)
            else:
                ends = ()
            for node in ends:
                incident.setdefault(node, []).append(entry)
                if entry.traversable:
                    walkable.setdefault(node, []).append(relation)  # type: ignore[arg-type]
        self._walkable = {node: sorted(found, key=lambda r: (r.predicate, r.ref.relation_id))
                          for node, found in walkable.items()}
        self._incident = incident

    @classmethod
    def build(cls, campaign_id: str, relations: Iterable[Relation], instances: Iterable[InstanceRef], *,
              unsupported: Iterable[UnsupportedRelation] = (),
              undecodable: Iterable[tuple[RelationRef, str]] = ()) -> GraphIndex:
        try:
            validate_segment(campaign_id, "campaign_id")
        except ValueError:
            raise GraphError("invalid_field") from None
        known = frozenset(instances)
        if not all(type(ref) is InstanceRef for ref in known):
            raise GraphError("invalid_field")
        if any(ref.campaign_id != campaign_id for ref in known):
            raise GraphError("campaign_mismatch")
        entries: dict[str, RelationEntry] = {}
        problems: list[GraphProblem] = []

        def admit(ref: object, entry: RelationEntry) -> None:
            if type(ref) is not RelationRef:
                raise GraphError("invalid_field")
            if ref.campaign_id != campaign_id:
                raise GraphError("campaign_mismatch", (ref.relation_id,))
            if ref.relation_id in entries:
                raise GraphError("duplicate_id", (ref.relation_id,))
            entries[ref.relation_id] = entry

        decoded: list[Relation] = []
        for relation in relations:
            if type(relation) is not Relation:
                raise GraphError("invalid_field")
            missing = not relation.endpoints() <= known
            admit(relation.ref, RelationEntry(relation.ref, relation, retracted=relation.retraction is not None,
                                              ended=relation.ending is not None, missing_endpoint=missing,
                                              reopened=bool(relation.reopenings)))
            if missing:
                problems.append(GraphProblem("missing_endpoint", (relation.ref.relation_id,)))
            decoded.append(relation)
        for found in unsupported:
            if type(found) is not UnsupportedRelation:
                raise GraphError("invalid_field")
            admit(found.ref, RelationEntry(found.ref, found, unsupported=True))
            problems.append(GraphProblem("unknown_predicate", (found.ref.relation_id,)))
        for item in undecodable:
            ref, reason = item
            if type(reason) is not str:
                raise GraphError("invalid_field")
            admit(ref, RelationEntry(ref, None, undecodable=reason))
            problems.append(GraphProblem("undecodable", (ref.relation_id,)))
        facts: dict[tuple, list[Relation]] = {}  # only relations recording the same fact can overlap
        for relation in sorted(decoded, key=lambda r: r.ref.relation_id):
            ends = relation.endpoints() if relation.symmetric else (relation.subject, relation.object)
            facts.setdefault((relation.predicate, ends), []).append(relation)
        for same in facts.values():
            for i, first in enumerate(same):  # every overlapping pair, reported on its own
                for second in same[i + 1:]:
                    if first.overlaps(second):
                        problems.append(GraphProblem("duplicate", (first.ref.relation_id, second.ref.relation_id)))
        return cls(campaign_id, known, entries, tuple(sorted(problems, key=GraphProblem.sort_key)))

    # -- validation helpers ------------------------------------------------------------------------

    def _node(self, node: object, must_exist: bool) -> InstanceRef:
        if type(node) is not InstanceRef:
            raise GraphError("invalid_field")
        if node.campaign_id != self.campaign_id:
            raise GraphError("campaign_mismatch")
        if must_exist and node not in self.instances:
            raise GraphError("unknown_instance")
        return node

    def _walk_from(self, node: InstanceRef, as_of: int, predicates: frozenset[str] | None,
                   direction: str) -> list[tuple[Relation, InstanceRef, bool]]:
        found = []
        for relation in self._walkable.get(node, ()):
            if not relation.holds_at(as_of) or (predicates is not None and relation.predicate not in predicates):
                continue
            incidence = _incidence(relation, node, direction)
            if incidence is not None:
                found.append((relation, *incidence))
        return found

    # -- queries -----------------------------------------------------------------------------------

    def relations(self, node: InstanceRef, *, as_of: int, requested_by: str, predicates: Iterable[str] | None = None,
                  direction: str = "any") -> tuple[Relation, ...]:
        """Relations walkable at ``as_of`` incident to ``node``, ordered by (predicate, id)."""
        require_director(requested_by)
        as_of = check_as_of(as_of)
        direction = _check_direction(direction)
        names = _check_predicates(predicates)
        node = self._node(node, must_exist=True)
        return tuple(relation for relation, _, _ in self._walk_from(node, as_of, names, direction))

    def history(self, node: InstanceRef, *, requested_by: str) -> tuple[RelationEntry, ...]:
        """Every relation file naming ``node``, at any time, flagged; ``node`` need not exist any more."""
        require_director(requested_by)
        node = self._node(node, must_exist=False)

        def key(entry: RelationEntry) -> tuple:
            if isinstance(entry.relation, Relation):
                return (0, entry.relation.since.sequence, entry.ref.relation_id)
            return (1, 0, entry.ref.relation_id)

        return tuple(sorted(self._incident.get(node, ()), key=key))

    def relation(self, relation_id: str, *, requested_by: str) -> RelationEntry:
        require_director(requested_by)
        entry = self._entries.get(relation_id) if type(relation_id) is str else None
        if entry is None:
            raise GraphError("unknown_relation")
        return entry

    def traverse(self, start: InstanceRef, *, as_of: int, requested_by: str, predicates: Iterable[str] | None = None,
                 direction: str = "any", max_depth: int = 2, max_steps: int = 200) -> Traversal:
        """Bounded breadth-first walk; ``truncated`` says whether anything walkable was left out."""
        require_director(requested_by)
        as_of = check_as_of(as_of)
        direction = _check_direction(direction)
        names = _check_predicates(predicates)
        start = self._node(start, must_exist=True)
        max_depth = _check_bound(max_depth, MAX_DEPTH)
        max_steps = _check_bound(max_steps, MAX_STEPS)
        reached: dict[InstanceRef, int] = {start: 0}
        steps: list[Step] = []
        seen: set[str] = set()
        queue = deque([start])
        truncated = False
        while queue and not truncated:
            node = queue.popleft()
            depth = reached[node]
            if depth >= max_depth:
                continue
            for relation, target, forward in self._walk_from(node, as_of, names, direction):
                rid = relation.ref.relation_id
                if rid in seen:
                    continue
                if len(steps) == max_steps:
                    truncated = True
                    break
                seen.add(rid)
                steps.append(Step(rid, relation.predicate, node, target, depth + 1, forward))
                if target not in reached:
                    reached[target] = depth + 1
                    queue.append(target)
        if not truncated:
            truncated = any(
                relation.ref.relation_id not in seen
                for node, depth in reached.items() if depth == max_depth
                for relation, _, _ in self._walk_from(node, as_of, names, direction))
        return Traversal(start, as_of, tuple(steps), MappingProxyType(reached), truncated)

    def recorded(self, predicate: str, subject: InstanceRef, object: InstanceRef, *, as_of: int,
                 requested_by: str) -> bool:
        """Whether a walkable relation records this fact at ``as_of``. False is "not recorded", never "false"."""
        require_director(requested_by)
        as_of = check_as_of(as_of)
        if type(predicate) is not str or predicate not in PREDICATES:
            raise GraphError("unknown_predicate")
        subject, object = self._node(subject, False), self._node(object, False)
        return any(self._matches(relation, predicate, subject, object)
                   for relation, _, _ in self._walk_from(subject, as_of, frozenset({predicate}), "any"))

    @staticmethod
    def _matches(relation: Relation, predicate: str, subject: InstanceRef, object: InstanceRef) -> bool:
        if relation.predicate != predicate:
            return False
        if relation.symmetric:
            return relation.endpoints() == frozenset({subject, object})
        return (relation.subject, relation.object) == (subject, object)

    def _matching(self, predicate: str, subject: InstanceRef, object: InstanceRef, as_of: int) -> tuple[str, ...]:
        """Ids of walkable relations recording this fact at ``as_of``. Package-internal: callers check the director."""
        if subject not in self.instances:
            return ()
        return tuple(relation.ref.relation_id
                     for relation, _, _ in self._walk_from(subject, as_of, frozenset({predicate}), "any")
                     if self._matches(relation, predicate, subject, object))

    def to_json(self, *, requested_by: str) -> dict:
        """A deterministic JSON-compatible form; a pure function of the canonical inputs."""
        require_director(requested_by)
        relations = []
        for rid in sorted(self._entries):
            entry = self._entries[rid]
            item: dict = {"id": rid, "retracted": entry.retracted, "ended": entry.ended,
                          "missing_endpoint": entry.missing_endpoint, "unsupported": entry.unsupported}
            if isinstance(entry.relation, Relation):
                item["payload"] = relation_to_payload(entry.relation)
            elif isinstance(entry.relation, UnsupportedRelation):
                item["payload"] = thaw(entry.relation.payload)
            else:
                item["undecodable"] = entry.undecodable
            relations.append(item)
        return {"campaign_id": self.campaign_id, "instances": sorted(str(ref) for ref in self.instances),
                "relations": relations,
                "problems": [{"code": p.code, "relation_ids": list(p.relation_ids)} for p in self.problems]}
