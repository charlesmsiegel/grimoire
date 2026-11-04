"""What an actor thinks is connected: per-node claims from its own projection only (Sprint 017).

Claim shapes give 012 propositions a typed relational reading. ``claims_about`` reads only an
``ActorKnowledge`` value, one node at a time, and never names the other endpoint. The director
compares claims with canonical relations through ``claim_matches`` and validates 012 subject
citations through ``check_subjects``. All fixtures are synthetic.
"""

from __future__ import annotations

import dataclasses

import pytest

from grimoire.core import (
    DIRECTOR, ActorKnowledge, AuthorityError, Belief, ControlAssignment, Disclosure, Event, InstanceRef, Knowledge,
    Moment, Proposition, Report, Ruling, Subject, actor_knowledge, director_knowledge, knowledge_about,
    player_knowledge,
)
from grimoire.core.identity import RelationRef
from grimoire.core.json_value import JsonObject
from grimoire.graph import (
    ClaimSet, ClaimShape, GraphError, GraphIndex, GraphProblem, Relation, Retraction, UnsupportedRelation,
    check_subjects, claim_matches, claims_about,
)

from knowledge_fixtures import leaks

C = "c"


def actor(name: str, kind: str = "character") -> InstanceRef:
    return InstanceRef(C, kind, name)


TOMAS, INES, ORRIN, WREN, MOLE = (actor(n) for n in ("tomas", "ines", "orrin", "wren", "mole"))
GUILD, HARBOR = actor("ashen-guild", "organization"), actor("harbor", "place")
GHOST = actor("ghost")
NODES = (TOMAS, INES, ORRIN, WREN, MOLE, GUILD, HARBOR, GHOST)


def at(sequence: int) -> Moment:
    return Moment(sequence)


def about(*refs: InstanceRef) -> tuple[Subject, ...]:
    return tuple(Subject("instance", str(ref)) for ref in refs)


def propositions() -> list[Proposition]:
    return [
        Proposition(C, "p-ines-guild", "Ines is in the Ashen Guild", at(1), "rumour", about(INES, GUILD)),
        Proposition(C, "p-meet", "Ines meets the guild at the harbor", at(1), "rumour", about(INES, GUILD, HARBOR)),
        Proposition(C, "p-wren-guild", "Wren belongs to the Ashen Guild", at(1), "director", about(WREN, GUILD)),
        Proposition(C, "p-met", "Tomas and Wren have met", at(1), "director", about(TOMAS, WREN)),
        Proposition(C, "p-cold", "The harbor is cold", at(1), "director", about(HARBOR)),
    ]


def shapes() -> list[ClaimShape]:
    return [
        ClaimShape(C, "p-ines-guild", "member_of", INES, GUILD),
        ClaimShape(C, "p-meet", "member_of", INES, GUILD),
        ClaimShape(C, "p-wren-guild", "member_of", WREN, GUILD),
        ClaimShape(C, "p-met", "has_met", WREN, TOMAS),  # symmetric: order is irrelevant
    ]


def report(eid, holder, pid, seq, sender, attributed_to=None, *, confirmed=True, addressed=False) -> Report:
    return Report(C, eid, holder, pid, "told", at(seq), "director", confirmed=confirmed, sender=sender,
                  attributed_to=attributed_to, addressed=addressed, sent_at=at(seq) if addressed else None)


def knowledge(*, sender=MOLE, rulings=(), extra_evidence=(), extra_beliefs=(), events=(), disclosures=(),
              beliefs_by="director") -> Knowledge:
    evidence = [
        report("r-1", TOMAS, "p-ines-guild", 3, sender, ORRIN),  # a forged attribution: Mole wrote it
        report("r-2", TOMAS, "p-meet", 4, ORRIN, ORRIN, addressed=True),
        report("r-3", INES, "p-met", 2, ORRIN, ORRIN),
        report("r-4", TOMAS, "p-wren-guild", 5, ORRIN, ORRIN, confirmed=False),
        *extra_evidence,
    ]
    beliefs = [
        Belief(C, "b-1", TOMAS, "p-ines-guild", "suspected", at(3), "director", ("r-1",)),
        Belief(C, "b-i1", INES, "p-met", "believed", at(2), beliefs_by, ("r-3",)),
        Belief(C, "b-i2", INES, "p-met", "rejected", at(5), beliefs_by, ("r-3",), supersedes="b-i1"),
        *extra_beliefs,
    ]
    return Knowledge(C, events=events, propositions=propositions(), rulings=rulings, evidence=evidence,
                     beliefs=beliefs, disclosures=disclosures)


def claim_set(k: Knowledge | None = None) -> ClaimSet:
    return ClaimSet(k or knowledge(), shapes())


def view(who: InstanceRef, as_of: int = 9, k: Knowledge | None = None) -> ActorKnowledge:
    return actor_knowledge(k or knowledge(), who, as_of=as_of)


def pids(found) -> list[str]:
    return [item.proposition_id for item in found.items]


# -- the claim set ----------------------------------------------------------------------------

@pytest.mark.parametrize("make, reason", [
    (lambda: ClaimSet(knowledge(), [ClaimShape(C, "p-none", "member_of", INES, GUILD)]), "unknown_proposition"),
    (lambda: ClaimSet(knowledge(), [ClaimShape("other", "p-met", "has_met",
                                               InstanceRef("other", "character", "a"),
                                               InstanceRef("other", "character", "b"))]), "campaign_mismatch"),
    (lambda: ClaimShape(C, "p-met", "has_met", TOMAS, InstanceRef("other", "character", "b")), "campaign_mismatch"),
    (lambda: ClaimShape(C, "p-met", "loves", TOMAS, WREN), "unknown_predicate"),
    (lambda: ClaimShape(C, "p-met", "has_met", TOMAS, TOMAS), "self_relation"),
    (lambda: ClaimShape(C, "p-met", "has_met", "tomas", WREN), "invalid_field"),
    (lambda: ClaimSet(knowledge(), [ClaimShape(C, "p-cold", "located_in", HARBOR, GUILD)]), "not_overt"),
    (lambda: ClaimSet(knowledge(), [ClaimShape(C, "p-met", "has_met", TOMAS, WREN),
                                    ClaimShape(C, "p-met", "kin_of", TOMAS, WREN)]), "duplicate_claim"),
    (lambda: ClaimSet("ledger", []), "invalid_field"),
    (lambda: ClaimSet(knowledge(), ["p-met"]), "invalid_field"),
])
def test_claim_set_refusals(make, reason):
    with pytest.raises(GraphError) as caught:
        make()
    assert caught.value.reason == reason


def test_claim_set_keeps_no_ledger():
    claims = claim_set()
    assert set(ClaimSet.__slots__) == {"campaign_id", "shapes"}
    assert claims.campaign_id == C and set(claims.shapes) == {"p-ines-guild", "p-meet", "p-wren-guild", "p-met"}
    with pytest.raises(TypeError):
        claims.shapes["p-x"] = shapes()[0]  # type: ignore[index]


# -- claims about a node --------------------------------------------------------------------------

def test_false_membership_is_a_claim():
    found = claims_about(view(TOMAS), claim_set(), INES)
    assert (found.actor, found.as_of, found.node) == (TOMAS, 9, INES)
    assert pids(found) == ["p-ines-guild", "p-meet"]
    rumour = found.items[0]
    assert (rumour.predicate, rumour.position, rumour.stance, rumour.inherited_stance) == (
        "member_of", "subject", "suspected", None)
    assert rumour.text == "Ines is in the Ashen Guild"
    assert [e.evidence_id for e in rumour.heard] == ["r-1"] and rumour.heard[0].attributed_to == ORRIN
    assert rumour.sent == ()
    assert leaks(found, str(MOLE), "mole", str(GUILD), "ashen-guild", "rumour", "refuted", "established") == []
    assert [i.position for i in claims_about(view(TOMAS), claim_set(), GUILD).items] == ["object", "object"]
    assert {f.name for f in dataclasses.fields(type(rumour))} == {
        "proposition_id", "predicate", "position", "text", "heard", "sent", "stance", "inherited_stance"}


def test_refuted_claim_is_identical_item():
    refuted = knowledge(rulings=[Ruling(C, "rule-1", "p-ines-guild", "refuted", at(4), "director")])
    assert claims_about(view(TOMAS, k=refuted), claim_set(refuted), INES) == claims_about(
        view(TOMAS), claim_set(), INES)


def test_invariance_under_director_only_material():
    event = Event(C, "e-1", "the guild met in secret", at(1), "director", (WREN, GUILD))
    variants = [
        knowledge(),
        knowledge(rulings=[Ruling(C, "rule-1", "p-ines-guild", "refuted", at(4), "director")]),
        knowledge(sender=WREN),  # a different true sender
        knowledge(extra_evidence=[report("r-9", TOMAS, "p-cold", 2, ORRIN, confirmed=False),
                                  report("r-8", ORRIN, "p-wren-guild", 2, WREN, WREN)]),
        knowledge(extra_beliefs=[Belief(C, "b-o", ORRIN, "p-wren-guild", "believed", at(2), "director",
                                        ("r-8",))],
                  extra_evidence=[report("r-8", ORRIN, "p-wren-guild", 2, WREN, WREN)]),
        knowledge(events=[event], disclosures=[Disclosure(C, "d-1", "e-1", at(2), "director")]),
    ]
    for node in NODES:
        answers = {claims_about(view(TOMAS, k=k), claim_set(k), node) for k in variants}
        assert len(answers) == 1, node


def _held(k: Knowledge, who: InstanceRef, node: InstanceRef, as_of: int) -> set[str]:
    found = knowledge_about(k, who, Subject("instance", str(node)), as_of=as_of)
    return ({e.proposition_id for e in found.evidence if e.kind == "report"}
            | {s.proposition_id for s in found.sent} | {b.proposition_id for b in found.current})


def test_equivalence_with_knowledge_about():
    k, claims = knowledge(), claim_set()
    for who in (TOMAS, INES, ORRIN, WREN, MOLE):
        for node in NODES:
            for as_of in (0, 3, 4, 9):
                expected = {pid for pid in _held(k, who, node, as_of)
                            if pid in claims.shapes and node in (claims.shapes[pid].subject, claims.shapes[pid].object)}
                assert set(pids(claims_about(view(who, as_of, k), claims, node))) == expected, (who, node, as_of)
    assert "p-meet" in _held(k, TOMAS, HARBOR, 9)  # about the harbor, but the harbor is no endpoint
    assert pids(claims_about(view(TOMAS), claims, HARBOR)) == []


def test_symmetric_shape_order_is_irrelevant():
    claims = claim_set()
    for node in (TOMAS, WREN):
        found = claims_about(view(INES), claims, node)
        assert pids(found) == ["p-met"] and found.items[0].position == "either"
        assert found.items[0].stance == "rejected"  # the current belief; the superseded one is no item


def test_unknown_and_unheld_nodes_are_indistinguishable():
    claims = claim_set()
    assert claims_about(view(TOMAS), claims, GHOST).items == claims_about(view(TOMAS), claims, WREN).items == ()


@pytest.mark.parametrize("bad", [
    lambda: knowledge(), lambda: director_knowledge(knowledge(), as_of=9, requested_by=DIRECTOR),
    lambda: GraphIndex.build(C, (), ()), lambda: None,
])
def test_claims_about_needs_exactly_an_actor_knowledge(bad):
    with pytest.raises(GraphError) as caught:
        claims_about(bad(), claim_set(), INES)
    assert caught.value.reason == "not_actor_knowledge"


def test_claims_about_refuses_a_subclass_and_other_campaigns():
    class Sneaky(ActorKnowledge):
        __slots__ = ()

    original = view(TOMAS)
    sneaky = Sneaky(*(getattr(original, f.name) for f in dataclasses.fields(ActorKnowledge)))
    with pytest.raises(GraphError) as caught:
        claims_about(sneaky, claim_set(), INES)
    assert caught.value.reason == "not_actor_knowledge"
    with pytest.raises(GraphError) as caught:
        claims_about(original, claim_set(), InstanceRef("other", "character", "ines"))
    assert caught.value.reason == "campaign_mismatch"
    other = Knowledge("other")
    foreign = actor_knowledge(other, InstanceRef("other", "character", "x"), as_of=1)
    with pytest.raises(GraphError) as caught:
        claims_about(foreign, claim_set(), INES)
    assert caught.value.reason == "campaign_mismatch"


def test_sent_and_inherited():
    sender = claims_about(view(ORRIN), claim_set(), INES)
    assert pids(sender) == ["p-meet"] and [s.evidence_id for s in sender.items[0].sent] == ["r-2"]
    assert sender.items[0].heard == () and sender.items[0].stance is None
    k = knowledge()
    controls = [ControlAssignment(C, "ines", "player:p1")]
    pc_view = player_knowledge(k, "player:p1", controls, as_of=9).actors[0]
    found = claims_about(pc_view, claim_set(k), TOMAS)
    assert pids(found) == ["p-met"]
    assert (found.items[0].stance, found.items[0].inherited_stance) == (None, "rejected")


def test_superseded_beliefs_are_not_items():
    found = claims_about(view(INES, as_of=9), claim_set(), WREN)
    assert found.items[0].stance == "rejected"
    early = claims_about(view(INES, as_of=3), claim_set(), WREN)
    assert early.items[0].stance == "believed"


def test_items_are_ordered_by_predicate_then_proposition():
    k = knowledge(extra_evidence=[report("r-5", TOMAS, "p-met", 6, ORRIN, ORRIN)])
    found = claims_about(view(TOMAS, k=k), claim_set(k), WREN)
    assert [(i.predicate, i.proposition_id) for i in found.items] == [("has_met", "p-met")]
    guild = claims_about(view(TOMAS, k=k), claim_set(k), GUILD)
    assert [i.proposition_id for i in guild.items] == sorted(i.proposition_id for i in guild.items)


# -- director comparisons and subject checks ------------------------------------------------------

def relation(rid, predicate, subject, obj, since=3, retracted=False) -> Relation:
    return Relation(RelationRef(C, rid), predicate, subject, obj, Moment(since), "director", "t",
                    retraction=Retraction("director", "t") if retracted else None)


def graph(*relations, unsupported=(), undecodable=()) -> GraphIndex:
    return GraphIndex.build(C, relations, NODES[:-1], unsupported=unsupported, undecodable=undecodable)


def test_claim_matches():
    index = graph(relation("r-1", "member_of", WREN, GUILD))
    claims = claim_set()
    assert claim_matches(index, claims, "p-wren-guild", as_of=9, requested_by=DIRECTOR) == ("r-1",)
    assert claim_matches(index, claims, "p-wren-guild", as_of=2, requested_by=DIRECTOR) == ()
    assert claim_matches(index, claims, "p-ines-guild", as_of=9, requested_by=DIRECTOR) == ()
    with pytest.raises(AuthorityError):
        claim_matches(index, claims, "p-wren-guild", as_of=9, requested_by="player:p1")
    with pytest.raises(GraphError) as caught:
        claim_matches(index, claims, "p-cold", as_of=9, requested_by=DIRECTOR)
    assert caught.value.reason == "unknown_claim"


def test_check_subjects():
    index = graph(relation("r-1", "member_of", WREN, GUILD, since=3), relation("r-2", "owns", WREN, HARBOR,
                                                                                  retracted=True),
                  unsupported=(UnsupportedRelation(RelationRef(C, "r-u"), "mentors", JsonObject({}), ()),),
                  undecodable=((RelationRef(C, "r-x"), "malformed"),))
    rel = lambda rid: Subject("relation", rid)  # noqa: E731
    k = Knowledge(C, propositions=[
        Proposition(C, "p-early", "the secret pact", at(1), "d", (rel("r-1"),)),
        Proposition(C, "p-late", "the pact", at(5), "d", (rel("r-1"), Subject("definition", "world:x/character/y"))),
        Proposition(C, "p-bad", "lost things", at(5), "d", (rel("r-none"), rel("r-2"), rel("r-u"), rel("r-x"),
                                                             Subject("instance", str(GHOST)))),
    ], rulings=[Ruling(C, "rule-1", "p-early", "established", at(2), "d", grounds=(rel("r-1"),))])
    assert check_subjects(k, index, requested_by=DIRECTOR) == (
        GraphProblem("about_future_relation", ("r-1",), "proposition:p-early", "relation:r-1"),
        GraphProblem("retracted_relation", ("r-2",), "proposition:p-bad", "relation:r-2"),
        GraphProblem("ruling_cites_future_relation", ("r-1",), "ruling:rule-1", "relation:r-1"),
        GraphProblem("undecodable_relation", ("r-x",), "proposition:p-bad", "relation:r-x"),
        GraphProblem("unknown_instance", (), "proposition:p-bad", f"instance:{GHOST}"),
        GraphProblem("unknown_relation", ("r-none",), "proposition:p-bad", "relation:r-none"),
        GraphProblem("unsupported_relation", ("r-u",), "proposition:p-bad", "relation:r-u"),
    )
    assert check_subjects(knowledge(), graph(), requested_by=DIRECTOR) == ()
    with pytest.raises(AuthorityError):
        check_subjects(k, index, requested_by="player:p1")
    with pytest.raises(GraphError) as caught:
        check_subjects(Knowledge("other"), index, requested_by=DIRECTOR)
    assert caught.value.reason == "campaign_mismatch"
