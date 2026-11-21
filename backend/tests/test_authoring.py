"""World authoring proposals (087): pure values, namesake keys and selecting what a decision accepts."""

from __future__ import annotations

import pytest

from grimoire.authoring import (
    AuthoringError, AuthoringRequest, Candidate, Decision, Duplicate, Link, LinkProblem, Proposal, name_key, select,
)
from grimoire.core.identity import SourceRef

REALM = "world:realm"
COAST = SourceRef(REALM, "location", "coast", "r1")
REQUEST = AuthoringRequest("r", REALM, "Towns on the coast")
A = Candidate("a", "location", "Brinewick", "individual")
B = Candidate("b", "location", "Gullhaven", "template")
G = Candidate("g", "organization", "Lamplighters", "individual")
IDEA = Candidate("idea", "location", "A drowned tower", "brainstorm")
C = Candidate("c", "location", "Tidemoor", "individual")


def proposal(*links: Link, candidates=(A, B, C, G, IDEA)) -> Proposal:
    return Proposal("p", REQUEST, tuple(candidates), tuple(links))


@pytest.mark.parametrize("left,right", [
    ("The Lamplighters' Guild", "Lamplighters Guild"),
    ("Guild of the Lamplighters", "lamplighters guild"),
    ("ＢＲＩＮＥＷＩＣＫ", "brinewick"),
    ("Mara's Rest", "Mara Rest"),
    ("Lamplighter's Guild", "Lamplighters Guild"),
    ("Café Noir", "Cafe Noir"),
])
def test_namesakes_share_a_key(left, right):
    assert name_key(left) == name_key(right)


@pytest.mark.parametrize("left,right", [("Brinewick", "Brine Wick"), ("North Gate", "Gate"),
                                        ("Glass Tower", "Glas Tower")])
def test_different_names_do_not(left, right):
    assert name_key(left) != name_key(right)


def test_a_name_made_only_of_ignored_words_still_has_a_key():
    assert name_key("The") == ("the",) and name_key("The") != name_key("Of")


@pytest.mark.parametrize("build,reason", [
    (lambda: Candidate("a", "species", "Goblin", "template"), "unsupported_kind"),
    (lambda: Candidate("a", "location", "Town", "canon"), "invalid_role"),
    (lambda: Candidate("A", "location", "Town", "individual"), "invalid_key"),
    (lambda: Candidate("a", "location", " ", "individual"), "invalid_text"),
    (lambda: Candidate("a" * 61, "location", "Town", "individual"), "invalid_key"),
    (lambda: Link("l" * 61, "road", "a", "b"), "invalid_link_id"),
    (lambda: Proposal("p" * 61, REQUEST, (A,)), "invalid_proposal_id"),
    (lambda: Candidate("a", "location", "bad\ud800", "individual"), "invalid_text"),
    (lambda: Link("l", "rivalry", "a", "b"), "unsupported_relation"),
    (lambda: Link("l", "road", "a", "a"), "self_link"),
    (lambda: Link("l", "road", "a", SourceRef(REALM, "location", "coast")), "unpinned_endpoint"),
    (lambda: proposal(Link("l", "road", "a", "zz")), "unknown_endpoint"),
    (lambda: proposal(Link("l", "road", "a", "g")), "endpoint_not_place"),
    (lambda: proposal(Link("l", "road", "b", "a")), "template_endpoint"),
    (lambda: proposal(Link("l", "road", "a", SourceRef("world:other", "location", "x", "r1"))), "foreign_endpoint"),
    (lambda: proposal(Link("l", "road", "a", "c"), Link("m", "road", "c", "a")), "duplicate_road"),
    (lambda: proposal(Link("l", "road", "a", "c"), Link("l", "within", "a", "c")), "duplicate_link_id"),
    (lambda: proposal(Link("l", "within", "a", "c"), Link("m", "within", "a", COAST)), "two_parents"),
    (lambda: proposal(Link("l", "within", "a", "c"), Link("m", "within", "c", "a")), "containment_cycle"),
    (lambda: proposal(candidates=(A, A)), "duplicate_key"),
    (lambda: proposal(candidates=()), "candidate_bound"),
    (lambda: Proposal("p", AuthoringRequest("r", REALM, "x", max_candidates=1), (A, B)), "candidate_bound"),
    (lambda: AuthoringRequest("r", REALM, "x", max_candidates=17), "invalid_bound"),
    (lambda: AuthoringRequest("r", "campaign:c", "x"), "invalid_source"),
    (lambda: AuthoringRequest("r", "character:wren", "x"), "invalid_source"),
    (lambda: AuthoringRequest("r", "addon:fan", "x"), "invalid_source"),
    (lambda: AuthoringRequest("r", "plain", "x"), "invalid_source"),
    (lambda: AuthoringRequest("r", REALM, "x", ("same", "same")), "duplicate_constraint"),
    (lambda: Decision((), "human:owner"), "nothing_accepted"),
    (lambda: Decision(("a",), "human:owner", ("b",)), "distinct_not_accepted"),
])
def test_values_refuse_what_cannot_be_reviewed(build, reason):
    with pytest.raises(AuthoringError) as refused:
        build()
    assert refused.value.reason == reason


def test_select_keeps_links_between_accepted_candidates_and_existing_places_only():
    links = (Link("ac", "road", "a", "c"), Link("a-in", "within", "a", COAST), Link("idea-road", "road", "idea", "a"),
             Link("c-in", "within", "c", COAST))
    chosen = select(proposal(*links), Decision(("c", "a"), "human:owner"), (),
                    (LinkProblem("c-in", "endpoint_moved"),))
    assert [candidate.key for candidate in chosen.accepted] == ["c", "a"]
    assert [link.link_id for link in chosen.links] == ["ac", "a-in"]
    assert [(link.link_id, why) for link, why in chosen.dropped] == [("idea-road", "rejected_endpoint"),
                                                                     ("c-in", "endpoint_moved")]
    assert [candidate.key for candidate in chosen.discarded] == ["b", "g", "idea"]


def test_select_refuses_unknown_brainstorm_and_unacknowledged_duplicates():
    flagged = (Duplicate("g", existing=SourceRef(REALM, "organization", "guild", "r1")),)
    with pytest.raises(AuthoringError, match="unknown_candidate"):
        select(proposal(), Decision(("zz",), "human:owner"), (), ())
    with pytest.raises(AuthoringError, match="brainstorm_not_storable"):
        select(proposal(), Decision(("idea",), "human:owner"), (), ())
    with pytest.raises(AuthoringError, match="duplicate_unacknowledged"):
        select(proposal(), Decision(("g",), "human:owner"), flagged, ())
    kept = select(proposal(), Decision(("g",), "human:owner", ("g",)), flagged, ())
    assert kept.surfaced == () and [c.key for c in kept.accepted] == ["g"]
    assert select(proposal(), Decision(("a",), "human:owner"), flagged, ()).surfaced == flagged
