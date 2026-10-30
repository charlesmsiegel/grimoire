"""The campaign roster bridges instance references and segment-keyed control (009)."""

from dataclasses import replace

import pytest
from entity_fixtures import index_of, ogre, plain_sword, vessa

from grimoire.core import (
    ALL_DOMAINS, DIRECTOR, PROTECTED_DOMAINS, AuthorityError, ControlAssignment, IdentityConflict,
    InstanceRef, NarrationClaim, NarrationContext, PlayerContribution, SourceRef, controller_of,
    narration_policy, review_narration, submit_contribution,
)
from grimoire.core.entity import EntityInstance
from grimoire.core.facets import Agent, FacetBundle, UnknownFacet
from grimoire.core.roster import CampaignRoster, ControlProblem, UnknownInstance
from grimoire.intent import RoutingContext, Utterance, route

ALICE = "human:alice"
CTX = NarrationContext("c1", "s1", "t1")
VESSA = InstanceRef("c1", "item", "vessa")
BLADE = InstanceRef("c1", "item", "blade")


def sword_roster(source=None, *extra):
    return CampaignRoster("c1", [EntityInstance(VESSA, source or vessa().ref.unpinned()),
                                 EntityInstance(BLADE, plain_sword().ref), *extra])


def sword_line(roster, text='"I will not be drawn."'):
    intent = route(Utterance(text), RoutingContext(roster.segment_for(VESSA), False))
    assert intent.kind == "pc_speech"
    return PlayerContribution("c1", "k1", "s1", "t1", intent.actor_id, ALICE, "speech", intent.text)


def speech_claims(line):
    return [
        NarrationClaim("c1", "verbatim", "voluntary_speech", line.text, subject_id="vessa", expresses="k1"),
        NarrationClaim("c1", "invented", "voluntary_speech", '"Fine, wield me."', subject_id="vessa"),
    ]


def outcome(review):
    return ({a.claim.claim_id: a.basis for a in review.accepted},
            {r.claim.claim_id: r.reason for r in review.rejected})


def test_sentient_sword_is_played_like_a_pc():
    index, roster = index_of(vessa(), plain_sword()), sword_roster()
    controls = (roster.assign_control(VESSA, ALICE, index),)
    assert controls == (ControlAssignment("c1", "vessa", ALICE),)
    line = sword_line(roster)
    assert line.text == "I will not be drawn."
    assert submit_contribution(line, controls) == line
    assert narration_policy(CTX, "vessa", controls).reserved == PROTECTED_DOMAINS
    review = review_narration(CTX, speech_claims(line), controls, contributions=[line])
    assert outcome(review) == ({"verbatim": "player:k1"}, {"invented": "pc_authorship"})


def test_control_switch_keeps_identity_and_restores_pc_rights():
    index, roster = index_of(vessa(), plain_sword()), sword_roster()
    before = (roster.instance(VESSA), index.resolve(vessa().ref))
    alice = (roster.assign_control(VESSA, ALICE, index),)
    line = sword_line(roster)
    director = (roster.assign_control(VESSA, DIRECTOR, index),)
    with pytest.raises(AuthorityError):
        submit_contribution(line, director)
    assert narration_policy(CTX, "vessa", director).director_may == ALL_DOMAINS
    back = (roster.assign_control(VESSA, ALICE, index),)
    assert back == alice
    assert narration_policy(CTX, "vessa", back).reserved == PROTECTED_DOMAINS
    review = review_narration(CTX, speech_claims(line), back, contributions=[line])
    assert outcome(review) == ({"verbatim": "player:k1"}, {"invented": "pc_authorship"})
    assert (roster.instance(VESSA), index.resolve(vessa().ref)) == before


def test_facets_never_grant_control():
    awakened = EntityInstance(BLADE, plain_sword().ref,
                              added=FacetBundle([Agent(), UnknownFacet("pc-control", {"player": ALICE})]))
    roster = CampaignRoster("c1", [awakened])
    assert controller_of("c1", "blade", ()) is None
    line = PlayerContribution("c1", "k1", "s1", "t1", roster.segment_for(BLADE), ALICE, "speech", "Hello.")
    with pytest.raises(AuthorityError):
        submit_contribution(line, ())
    assert roster.check_controls((), index_of(plain_sword())) == ()


def test_only_agent_instances_can_be_newly_controlled():
    index, roster = index_of(vessa(), plain_sword()), sword_roster()
    with pytest.raises(AuthorityError):
        roster.assign_control(BLADE, ALICE, index)
    with pytest.raises(UnknownInstance):
        roster.assign_control(InstanceRef("c1", "item", "ghost"), ALICE, index)


@pytest.mark.parametrize("not_an_instance", [ogre().ref, "vessa", "campaign:c1/item/vessa"])
def test_a_template_or_text_is_never_an_actor(not_an_instance):
    with pytest.raises(TypeError):
        sword_roster().assign_control(not_an_instance, ALICE, index_of(vessa(), plain_sword(), ogre()))


def test_losing_agency_upstream_is_reported_not_revoked():
    r1, r2 = vessa("r1"), vessa("r2", agent=False)
    roster = sword_roster(r1.ref.unpinned())
    controls = (roster.assign_control(VESSA, ALICE, index_of(r1, plain_sword())),)
    moved = index_of(r1, r2, plain_sword(), heads={r1.ref.unpinned(): "r2"})
    assert roster.check_controls(controls, moved) == (ControlProblem(controls[0], "not_agent"),)
    line = PlayerContribution("c1", "k1", "s1", "t1", "vessa", ALICE, "speech", "Still here.")
    assert submit_contribution(line, controls) == line
    with pytest.raises(AuthorityError):
        roster.assign_control(VESSA, ALICE, moved)


def test_control_problems_are_named():
    missing = EntityInstance(InstanceRef("c1", "item", "lost"), SourceRef("catalog:ghost", "item", "x", "r1"))
    roster = sword_roster(None, missing)
    controls = (ControlAssignment("c1", "nobody", ALICE), ControlAssignment("c2", "vessa", ALICE),
                ControlAssignment("c1", "lost", ALICE), ControlAssignment("c1", "vessa", ALICE))
    problems = roster.check_controls(controls, index_of(vessa(), plain_sword()))
    assert [(p.assignment.actor_id, p.reason, p.detail) for p in problems] == [
        ("nobody", "unknown_instance", None), ("vessa", "other_campaign", None),
        ("lost", "unresolved", "missing_source")]


def test_resolution_errors_propagate_from_assign_control():
    missing = EntityInstance(InstanceRef("c1", "item", "lost"), SourceRef("catalog:ghost", "item", "x", "r1"))
    with pytest.raises(LookupError):
        CampaignRoster("c1", [missing]).assign_control(missing.ref, ALICE, index_of(vessa()))


def test_roster_identity_rules():
    with pytest.raises(IdentityConflict):
        CampaignRoster("c1", [EntityInstance(VESSA, vessa().ref),
                              EntityInstance(InstanceRef("c1", "character", "vessa"), None, "Vessa")])
    with pytest.raises(IdentityConflict):
        CampaignRoster("c1", [EntityInstance(VESSA, vessa().ref), EntityInstance(VESSA, vessa().ref)])
    with pytest.raises(ValueError):
        CampaignRoster("c1", [EntityInstance(InstanceRef("c2", "item", "vessa"), vessa().ref)])
    with pytest.raises(ValueError):
        CampaignRoster("c1", ["vessa"])


def test_segments_and_references_convert_only_exactly():
    roster = sword_roster()
    assert roster.ref_for("vessa") == VESSA
    assert roster.segment_for(VESSA) == roster.segment_for("campaign:c1/item/vessa") == "vessa"
    with pytest.raises(ValueError):
        roster.ref_for("campaign:c1/item/vessa")
    with pytest.raises(UnknownInstance):
        roster.ref_for("ghost")
    for wrong in (InstanceRef("c1", "character", "vessa"), InstanceRef("c2", "item", "vessa")):
        with pytest.raises(UnknownInstance):
            roster.segment_for(wrong)
    with pytest.raises(ValueError):
        roster.segment_for("campaign:c1/item/Vessa")
    with pytest.raises(ValueError):
        roster.segment_for(vessa().ref)
    assert isinstance(UnknownInstance("x"), LookupError)


def test_a_roster_is_immutable_and_ordered():
    roster = sword_roster()
    assert [i.ref for i in roster.instances] == [BLADE, VESSA]
    with pytest.raises(AttributeError):
        roster.campaign_id = "c2"
    assert replace(roster.instance(VESSA)) == roster.instance(VESSA)
