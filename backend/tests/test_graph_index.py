"""The derived, director-only graph index and bounded traversal (Sprint 017)."""

from __future__ import annotations

import json

import pytest

from grimoire.core.authority import DIRECTOR, AuthorityError
from grimoire.core.identity import InstanceRef, RelationRef
from grimoire.core.json_value import JsonObject
from grimoire.core.knowledge import Moment
from grimoire.graph import (
    Ending, GraphError, GraphIndex, GraphProblem, Relation, Retraction, Step, UnsupportedRelation,
)

C = "c"
A, B, K, D, E, F = (InstanceRef(C, "character", n) for n in ("a", "b", "k", "d", "e", "f"))
GHOST = InstanceRef(C, "character", "ghost")
OTHER = InstanceRef("other", "character", "a")
NODES = (A, B, K, D, E, F)


def rel(rid, predicate, subject, obj, since=0, until=None, retracted=False) -> Relation:
    ending = Ending(Moment(until), "director", "t") if until is not None else None
    retraction = Retraction("director", "t") if retracted else None
    return Relation(RelationRef(C, rid), predicate, subject, obj, Moment(since), "director", "t", ending, retraction)


#: a cycle A-B-K, a duplicate B->D, and a tail D-E-F
CYCLIC = (
    rel("r-a", "knows", A, B), rel("r-b", "has_met", A, K), rel("r-c", "knows", B, D), rel("r-d", "knows", K, B),
    rel("r-e", "allied_with", D, E), rel("r-f", "knows", B, D, since=1), rel("r-g", "knows", E, F),
)


def index(relations=CYCLIC, instances=NODES, **kwargs) -> GraphIndex:
    return GraphIndex.build(C, relations, instances, **kwargs)


def walk(start=A, **kwargs):
    kwargs.setdefault("as_of", 5)
    return index().traverse(start, requested_by=DIRECTOR, **kwargs)


def ids(items) -> list[str]:
    return [item.relation_id if isinstance(item, Step) else item.ref.relation_id for item in items]


# -- problems ----------------------------------------------------------------------------------

def test_problems_reported():
    unsupported = UnsupportedRelation(RelationRef(C, "r-u"), "mentors", JsonObject({"predicate": "mentors"}), (A,))
    found = index(relations=(*CYCLIC, rel("r-m", "owns", A, GHOST)), unsupported=(unsupported,),
                  undecodable=((RelationRef(C, "r-x"), "invalid_field"),))
    assert found.problems == (
        GraphProblem("duplicate", ("r-c", "r-f")),
        GraphProblem("missing_endpoint", ("r-m",)),
        GraphProblem("undecodable", ("r-x",)),
        GraphProblem("unknown_predicate", ("r-u",)),
    )


def test_duplicates_are_reported_per_overlapping_pair():
    chain = (rel("r-1", "knows", A, B, 0, 5), rel("r-2", "knows", A, B, 4, 9), rel("r-3", "knows", A, B, 8))
    assert index(relations=chain).problems == (GraphProblem("duplicate", ("r-1", "r-2")),
                                              GraphProblem("duplicate", ("r-2", "r-3")))
    reversed_symmetric = (rel("r-1", "kin_of", A, B), rel("r-2", "kin_of", B, A))
    assert index(relations=reversed_symmetric).problems == (GraphProblem("duplicate", ("r-1", "r-2")),)
    assert index(relations=(rel("r-1", "knows", A, B), rel("r-2", "knows", B, A))).problems == ()


@pytest.mark.parametrize("build, reason", [
    (lambda: index(relations=(rel("r-1", "knows", A, B), rel("r-1", "knows", B, A))), "duplicate_id"),
    (lambda: index(undecodable=((RelationRef(C, "r-a"), "invalid_field"),)), "duplicate_id"),
    (lambda: GraphIndex.build("other", CYCLIC, ()), "campaign_mismatch"),
    (lambda: index(instances=(*NODES, OTHER)), "campaign_mismatch"),
    (lambda: index(relations=("r-1",)), "invalid_field"),
])
def test_build_refuses_inconsistent_inputs(build, reason):
    with pytest.raises(GraphError) as caught:
        build()
    assert caught.value.reason == reason


# -- relations, history and relation ----------------------------------------------------------

def test_relations_holds_and_direction():
    graph = index(relations=(*CYCLIC, rel("r-z", "owns", B, A, until=3), rel("r-y", "owns", A, B, retracted=True),
                             rel("r-m", "owns", A, GHOST)))

    def found(node, **kwargs):
        return ids(graph.relations(node, requested_by=DIRECTOR, **kwargs))

    assert found(B, as_of=5) == ["r-a", "r-c", "r-d", "r-f"]
    assert found(B, as_of=2) == ["r-a", "r-c", "r-d", "r-f", "r-z"]  # ordered by (predicate, id)
    assert found(B, as_of=5, direction="out") == ["r-c", "r-f"]
    assert found(B, as_of=5, direction="in") == ["r-a", "r-d"]
    assert found(A, as_of=5, direction="in") == ["r-b"]  # a symmetric relation is both out and in
    assert found(A, as_of=5, predicates={"knows"}) == ["r-a"]
    assert found(B, as_of=0, predicates=["knows"]) == ["r-a", "r-c", "r-d"]  # r-f starts at 1


def test_history_lists_everything_and_dangling_nodes():
    unsupported = UnsupportedRelation(RelationRef(C, "r-u"), "mentors", JsonObject({}), (A, GHOST))
    graph = index(relations=(rel("r-2", "owns", A, B, since=4, until=6), rel("r-1", "knows", A, B, retracted=True),
                             rel("r-3", "owns", A, GHOST, since=4)), unsupported=(unsupported,),
                  undecodable=((RelationRef(C, "r-x"), "malformed"),))
    history = graph.history(A, requested_by=DIRECTOR)
    assert [entry.ref.relation_id for entry in history] == ["r-1", "r-2", "r-3", "r-u"]
    assert [(e.retracted, e.ended, e.missing_endpoint, e.unsupported) for e in history] == [
        (True, False, False, False), (False, True, False, False), (False, False, True, False),
        (False, False, False, True)]
    assert [e.ref.relation_id for e in graph.history(GHOST, requested_by=DIRECTOR)] == ["r-3", "r-u"]
    assert graph.history(InstanceRef(C, "character", "nobody"), requested_by=DIRECTOR) == ()


def test_relation_by_id():
    unsupported = UnsupportedRelation(RelationRef(C, "r-u"), "mentors", JsonObject({}), ())
    graph = index(relations=(*CYCLIC, rel("r-m", "owns", A, GHOST)), unsupported=(unsupported,),
                  undecodable=((RelationRef(C, "r-x"), "invalid_until"),))
    for problem in graph.problems:
        for rid in problem.relation_ids:
            assert graph.relation(rid, requested_by=DIRECTOR).ref.relation_id == rid
    bad = graph.relation("r-x", requested_by=DIRECTOR)
    assert bad.relation is None and bad.undecodable == "invalid_until"
    assert graph.relation("r-a", requested_by=DIRECTOR).relation == CYCLIC[0]
    with pytest.raises(GraphError) as caught:
        graph.relation("r-none", requested_by=DIRECTOR)
    assert caught.value.reason == "unknown_relation"


# -- traversal --------------------------------------------------------------------------------

def test_traverse_bfs_order_steps_and_reached():
    found = walk(max_depth=2)
    assert found.steps == (
        Step("r-b", "has_met", A, K, 1, True), Step("r-a", "knows", A, B, 1, True),
        Step("r-d", "knows", K, B, 2, True),  # an edge into an already-reached node is still a step
        Step("r-c", "knows", B, D, 2, True), Step("r-f", "knows", B, D, 2, True),  # duplicates: one step each
    )
    assert dict(found.reached) == {A: 0, K: 1, B: 1, D: 2}
    assert found.start == A and found.as_of == 5 and found.truncated  # D's alliance with E was not walked


def test_traverse_forward_flag():
    found = walk(start=B, max_depth=1, direction="in")
    assert found.steps == (Step("r-a", "knows", B, A, 1, False), Step("r-d", "knows", B, K, 1, False))
    symmetric = walk(start=K, max_depth=1, predicates={"has_met"})
    assert symmetric.steps == (Step("r-b", "has_met", K, A, 1, True),)


def test_traverse_truncated_by_steps():
    found = walk(max_depth=2, max_steps=3)
    assert ids(found.steps) == ["r-b", "r-a", "r-d"] and found.truncated
    assert D not in found.reached


def test_traverse_truncated_by_depth():
    assert walk(max_depth=3).truncated  # E's acquaintance with F lies beyond
    found = walk(max_depth=4)
    assert not found.truncated and dict(found.reached)[F] == 4


def test_traverse_not_truncated_when_complete():
    found = walk(start=E, max_depth=8, direction="out")
    assert ids(found.steps) == ["r-e", "r-g"] and not found.truncated


def test_traverse_truncation_counts_an_edge_between_two_frontier_nodes():
    triangle = (rel("r-1", "knows", A, B), rel("r-2", "knows", A, K), rel("r-3", "has_met", B, K))
    found = index(relations=triangle).traverse(A, as_of=0, requested_by=DIRECTOR, max_depth=1)
    assert ids(found.steps) == ["r-1", "r-2"] and found.truncated


def test_traverse_from_isolated_node():
    graph = index(relations=(rel("r-1", "knows", A, B, until=2), rel("r-2", "knows", A, K, retracted=True)))
    found = graph.traverse(A, as_of=5, requested_by=DIRECTOR)
    assert found.steps == () and dict(found.reached) == {A: 0} and found.truncated is False


def test_traverse_as_of_filters():
    graph = index(relations=(rel("r-1", "member_of", A, B, since=3, until=12),))
    reached = {s: set(graph.traverse(A, as_of=s, requested_by=DIRECTOR).reached) for s in (2, 3, 11, 12)}
    assert reached == {2: {A}, 3: {A, B}, 11: {A, B}, 12: {A}}


def test_missing_endpoint_relations_are_never_walked():
    graph = index(relations=(rel("r-1", "knows", A, GHOST), rel("r-2", "knows", A, B)))
    assert ids(graph.traverse(A, as_of=0, requested_by=DIRECTOR).steps) == ["r-2"]


def test_deterministic_traversal_values():
    shuffled = index(relations=tuple(reversed(CYCLIC)), instances=tuple(reversed(NODES)))
    assert shuffled.traverse(A, as_of=5, requested_by=DIRECTOR) == walk()


# -- validation -------------------------------------------------------------------------------

@pytest.mark.parametrize("call, reason", [
    (lambda g: g.traverse(A, as_of=-1, requested_by=DIRECTOR), "invalid_as_of"),
    (lambda g: g.traverse(A, as_of=True, requested_by=DIRECTOR), "invalid_as_of"),
    (lambda g: g.relations(A, as_of="1", requested_by=DIRECTOR), "invalid_as_of"),
    (lambda g: g.traverse(A, as_of=1, requested_by=DIRECTOR, direction="up"), "invalid_direction"),
    (lambda g: g.relations(A, as_of=1, requested_by=DIRECTOR, predicates=set()), "invalid_predicates"),
    (lambda g: g.relations(A, as_of=1, requested_by=DIRECTOR, predicates="knows"), "invalid_predicates"),
    (lambda g: g.relations(A, as_of=1, requested_by=DIRECTOR, predicates=[3]), "invalid_predicates"),
    (lambda g: g.relations(A, as_of=1, requested_by=DIRECTOR, predicates={"loves"}), "unknown_predicate"),
    (lambda g: g.relations(OTHER, as_of=1, requested_by=DIRECTOR), "campaign_mismatch"),
    (lambda g: g.history(OTHER, requested_by=DIRECTOR), "campaign_mismatch"),
    (lambda g: g.relations(GHOST, as_of=1, requested_by=DIRECTOR), "unknown_instance"),
    (lambda g: g.traverse(GHOST, as_of=1, requested_by=DIRECTOR), "unknown_instance"),
    (lambda g: g.traverse(OTHER, as_of=1, requested_by=DIRECTOR), "campaign_mismatch"),
    (lambda g: g.traverse("campaign:c/character/a", as_of=1, requested_by=DIRECTOR), "invalid_field"),
    (lambda g: g.traverse(A, as_of=1, requested_by=DIRECTOR, max_depth=0), "invalid_bound"),
    (lambda g: g.traverse(A, as_of=1, requested_by=DIRECTOR, max_depth=9), "invalid_bound"),
    (lambda g: g.traverse(A, as_of=1, requested_by=DIRECTOR, max_steps=0), "invalid_bound"),
    (lambda g: g.traverse(A, as_of=1, requested_by=DIRECTOR, max_steps=2001), "invalid_bound"),
    (lambda g: g.traverse(A, as_of=1, requested_by=DIRECTOR, max_depth=True), "invalid_bound"),
    (lambda g: g.recorded("loves", A, B, as_of=1, requested_by=DIRECTOR), "unknown_predicate"),
    (lambda g: g.recorded("knows", A, OTHER, as_of=1, requested_by=DIRECTOR), "campaign_mismatch"),
])
def test_query_validation(call, reason):
    with pytest.raises(GraphError) as caught:
        call(index())
    assert caught.value.reason == reason


@pytest.mark.parametrize("call", [
    lambda g, who: g.relations(A, as_of=1, requested_by=who),
    lambda g, who: g.history(A, requested_by=who),
    lambda g, who: g.relation("r-a", requested_by=who),
    lambda g, who: g.traverse(A, as_of=1, requested_by=who),
    lambda g, who: g.recorded("knows", A, B, as_of=1, requested_by=who),
    lambda g, who: g.to_json(requested_by=who),
])
@pytest.mark.parametrize("who", ["player:x", "", None, "Director"])
def test_director_only(call, who):
    with pytest.raises(AuthorityError):
        call(index(), who)


def test_recorded_open_world():
    graph = index(relations=(rel("r-1", "kin_of", A, B), rel("r-2", "owns", A, K, until=3),
                             rel("r-3", "owns", B, K, retracted=True), rel("r-4", "owns", A, GHOST)))

    def recorded(predicate, s, o, as_of=5):
        return graph.recorded(predicate, s, o, as_of=as_of, requested_by=DIRECTOR)

    assert recorded("kin_of", A, B) and recorded("kin_of", B, A)
    assert recorded("owns", A, K, as_of=2) and not recorded("owns", A, K, as_of=3)
    assert not recorded("owns", K, A, as_of=2)
    assert not recorded("owns", B, K) and not recorded("owns", A, GHOST) and not recorded("knows", A, F)


def test_to_json_deterministic():
    unsupported = UnsupportedRelation(RelationRef(C, "r-u"), "mentors", JsonObject({"predicate": "mentors"}), ())
    kwargs = {"unsupported": (unsupported,), "undecodable": ((RelationRef(C, "r-x"), "malformed"),)}
    first = index(**kwargs).to_json(requested_by=DIRECTOR)
    second = index(relations=tuple(reversed(CYCLIC)), instances=tuple(reversed(NODES)), **kwargs).to_json(
        requested_by=DIRECTOR)
    assert first == second
    assert json.loads(json.dumps(first, sort_keys=True)) == first
    assert first["campaign_id"] == C and [r["id"] for r in first["relations"]][:2] == ["r-a", "r-b"]
