"""Fiction-first adjudication, pure rules (Sprint 029): frames, classification, decisions and codecs."""

from __future__ import annotations

import pytest

from grimoire.adjudication import (
    ABILITY_FACTOR, ABILITY_UNCERTAIN, AbilityFinding, ActionFrame, AdjudicationError, Decision, Factor, Outcome,
    Stake, assess_action, check_decision, decision_from_payload, decision_to_payload, frame_from_payload,
    frame_to_payload, ruling_from_payload, ruling_to_payload,
)
from grimoire.core.ability_use import AbilityRuling, assess_attempt
from grimoire.core.authority import (
    DIRECTOR, NarrationClaim, NarrationContext, review_narration,
)
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.core.model import ControlAssignment
from grimoire.store.jsonfmt import parse_strict, render

from ability_fixtures import effective, iris, light_bending, person
from adjudication_fixtures import (
    ACROBAT, ANA, CHAT, COLLAPSE, FALL, GAP, MARA, Q, SATCHEL, ability, bridge, moment, power_frame, vault,
)


def _assessment(frame=None, **kwargs):
    return assess_action(frame or bridge(**kwargs))


def _refused(assessment, decision) -> str:
    with pytest.raises(AdjudicationError) as caught:
        check_decision(assessment, decision)
    return caught.value.reason


# -- the proving scenario: a PC vaults a collapsing bridge ------------------------------------

def test_the_vault_is_uncertain_and_may_end_any_way_its_causes_support():
    assessment = _assessment()
    assert (assessment.classification, assessment.allowed) == ("uncertain", ("failure", "partial", "success"))
    assert {reason.code for reason in assessment.reasons} == {"hindered", "favoured"}
    # Each result needs a recorded cause in its direction, and a cost only from what was at stake.
    check_decision(assessment, Decision("success", "adjudicated", {"acrobat"}))
    check_decision(assessment, Decision("partial", "adjudicated", {"acrobat", "collapse"}, {"satchel"}))
    check_decision(assessment, Decision("failure", "adjudicated", {"gap"}, {"fall"}))


@pytest.mark.parametrize("decision, reason", [
    (Decision("failure", "adjudicated", {"rotten-plank"}, {"fall"}), "unknown_factor"),  # invented difficulty
    (Decision("failure", "adjudicated", {"gap"}, {"broken-leg"}), "undeclared_consequence"),
    (Decision("failure", "adjudicated", {"acrobat"}), "uncited_result"),  # her training is no cause of a fall
    (Decision("success", "adjudicated"), "uncited_result"),  # no favouring cause on record
    (Decision("partial", "adjudicated", {"acrobat"}, {"satchel"}), "uncited_result"),  # a cost needs a hindrance
    (Decision("success", "adjudicated", {"acrobat"}, {"satchel"}), "success_with_consequence"),
    (Decision("partial", "adjudicated", {"acrobat", "gap"}), "partial_without_cost"),
    (Decision("success", "rule", {"acrobat"}), "rule_mismatch"),  # nothing forces a risky result
    (Decision("success", "consent"), "result_not_allowed"),  # consent cannot turn a risk into a success
], ids=lambda value: getattr(value, "result", value))
def test_narrator_fiat_on_the_vault_is_refused(decision, reason):
    assert _refused(_assessment(), decision) == reason


def test_a_difficulty_cannot_be_established_in_the_same_breath_as_the_action():
    late = Factor("snap", "hinders", "A plank snaps underfoot.", 10, "event:e-snap")
    with pytest.raises(AdjudicationError) as caught:
        bridge(factors=(ACROBAT, GAP, late))
    assert caught.value.reason == "unestablished_factor"


def test_with_nothing_in_her_favour_the_vault_can_only_fail():
    assessment = _assessment(factors=(GAP, COLLAPSE))
    assert assessment.allowed == ("failure",)
    assert _refused(assessment, Decision("success", "adjudicated", {"gap"})) == "result_not_allowed"
    check_decision(assessment, Decision("failure", "adjudicated", {"collapse"}, {"fall"}))


# -- simple, impossible and consensual actions ------------------------------------------------

def test_an_unopposed_action_simply_succeeds():
    assessment = _assessment(factors=(ACROBAT,))
    assert (assessment.classification, assessment.allowed) == ("straightforward", ("success",))
    check_decision(assessment, Decision("success", "rule"))
    assert _refused(assessment, Decision("success", "adjudicated", {"acrobat"})) == "not_uncertain"
    assert _refused(assessment, Decision("failure", "rule", {"acrobat"})) == "rule_mismatch"


def test_a_hindered_action_with_nothing_at_stake_succeeds():
    assessment = _assessment(stakes=())
    assert assessment.classification == "straightforward"
    assert "nothing_at_stake" in {reason.code for reason in assessment.reasons}


def test_an_impossible_action_fails_by_rule():
    wall = Factor("sheer", "prevents", "The far side is a sheer wall with no ledge.", 3, "event:e-wall")
    assessment = _assessment(factors=(ACROBAT, wall))
    assert (assessment.classification, assessment.allowed) == ("impossible", ("failure",))
    check_decision(assessment, Decision("failure", "rule", {"sheer"}, {"fall"}))
    assert _refused(assessment, Decision("success", "adjudicated", {"acrobat"})) == "not_uncertain"


def test_consent_chooses_a_setback_never_a_better_result():
    easy = _assessment(factors=(ACROBAT,))
    check_decision(easy, Decision("partial", "consent", consequences={"satchel"}))  # the player wants it messy
    check_decision(easy, Decision("failure", "consent", consequences={"fall"}))
    check_decision(_assessment(), Decision("partial", "consent", consequences={"satchel"}))
    assert _refused(easy, Decision("success", "consent")) == "result_not_allowed"
    assert _refused(easy, Decision("failure", "consent", consequences={"broken-leg"})) == "undeclared_consequence"


# -- abilities (025) --------------------------------------------------------------------------

def _power(attempt, definition=None, rulings=()):
    entity = effective(definition or iris(), Q)
    return assess_attempt(attempt, entity, rulings)


def test_a_declared_power_favours_the_action():
    attempt = ability()
    assessment = assess_action(power_frame(attempt), _power(attempt))
    assert assessment.classification == "straightforward"
    assert assessment.bearing(ABILITY_FACTOR) == "favours"
    check_decision(assessment, Decision("success", "rule", {ABILITY_FACTOR}))


def test_a_power_beyond_its_limits_makes_the_action_impossible():
    attempt = ability(needs=("light", "force", "flight"), effect_id=None)  # flight by radiation pressure
    assessment = assess_action(power_frame(attempt), _power(attempt))
    assert (assessment.classification, assessment.bearing(ABILITY_FACTOR)) == ("impossible", "prevents")
    assert _refused(assessment, Decision("success", "rule")) == "rule_mismatch"


def test_a_stretch_needs_a_ruling_first_and_then_may_still_fail():
    attempt = ability(needs=("light", "force", "flight"), effect_id=None)
    unlimited = person("iris", "Iris", light_bending(force_limit=False))
    pending = assess_action(power_frame(attempt), _power(attempt, unlimited))
    assert (pending.classification, pending.allowed) == ("clarify", ())
    assert pending.reasons[0].code == "ability_ruling_needed"
    assert _refused(pending, Decision("failure", "consent")) == "needs_clarification"
    approval = AbilityRuling("r1", attempt, "extrapolation", "approved", "director")
    approved = assess_action(power_frame(attempt), _power(attempt, unlimited, [approval]))
    # An approved stretch works but is not sure to: it favours and hinders at once.
    assert approved.classification == "uncertain"
    assert (approved.bearing(ABILITY_FACTOR), approved.bearing(ABILITY_UNCERTAIN)) == ("favours", "hinders")
    check_decision(approved, Decision("partial", "adjudicated", {ABILITY_FACTOR, ABILITY_UNCERTAIN}, {"fall"}))
    refusal = AbilityRuling("r1", attempt, "extrapolation", "refused", "director")
    refused = assess_action(power_frame(attempt), _power(attempt, unlimited, [refusal]))
    assert (refused.classification, refused.bearing(ABILITY_FACTOR)) == ("impossible", "prevents")


def test_an_assessment_of_another_attempt_is_refused():
    attempt = ability()
    with pytest.raises(AdjudicationError) as caught:
        assess_action(power_frame(attempt), _power(ability(attempt_id="a-2")))
    assert caught.value.reason == "ability_mismatch"
    with pytest.raises(AdjudicationError):
        assess_action(bridge(), _power(attempt))


def test_a_recorded_finding_classifies_exactly_as_its_assessment():
    attempt = ability()
    live = _power(attempt)
    finding = AbilityFinding.of(live)
    assert finding.charges == (("light_bending", "eye_strain", "Every use leaves her eyes aching."),) \
        and finding.permitted
    assert assess_action(power_frame(attempt), finding) == assess_action(power_frame(attempt), live)


# -- 003: a narration retry stays consistent with the decided outcome -------------------------

def test_every_narration_retry_must_agree_with_the_recorded_outcome():
    frame = bridge()
    outcome = Outcome(frame, "uncertain", Decision("partial", "adjudicated", {"acrobat", "gap"}, {"satchel"}),
                      DIRECTOR)
    context = NarrationContext(Q, "s1", "t10")
    controls = [ControlAssignment(Q, "mara-1", ANA)]

    def review(result):
        claim = NarrationClaim(Q, "n1", "outcome", f"Mara's vault: {result}", subject_id="mara-1",
                               attempt_id="c10", result=result)
        return review_narration(context, [claim], controls, [vault()], resolutions=[outcome.resolution()])

    assert review("partial").accepted
    rejected = review("success").rejected
    assert [item.reason for item in rejected] == ["contradicts_resolution"]


# -- codecs -----------------------------------------------------------------------------------

def _stored(payload):
    return parse_strict(render(payload))  # exactly what a ledger reads back: frozen mappings and tuples


def test_frames_round_trip_and_only_their_canonical_form_decodes():
    frame = power_frame(ability(), factors=(ACROBAT,), stakes=(FALL, SATCHEL))
    for value, contribution in ((frame, None), (bridge(), vault())):
        payload = frame_to_payload(value, contribution)
        assert frame_from_payload(Q, _stored(payload)) == (value, contribution)
    payload = frame_to_payload(bridge(), None)
    payload["factors"] = list(reversed(payload["factors"]))
    with pytest.raises(AdjudicationError) as caught:
        frame_from_payload(Q, _stored(payload))
    assert caught.value.reason == "undecodable_entry"
    with pytest.raises(AdjudicationError):
        frame_from_payload("q-2", _stored(frame_to_payload(bridge(), None)))  # another campaign's


def test_rulings_and_decisions_round_trip():
    ruling = AbilityRuling("r1", ability(effect_id=None, needs=("light", "flight")), "extrapolation", "approved",
                           "director", note="Sun-sail, once.")
    assert ruling_from_payload(Q, _stored(ruling_to_payload(ruling, frozenset({"r0"})))) == (ruling,
                                                                                            frozenset({"r0"}))
    finding = AbilityFinding("light_bending", "extrapolation", "approved", "r1", True,
                             (("light_bending", "migraine", "A headache."), ("light_bending", "eye_strain", "Aching eyes.")))
    decision = Decision("partial", "consent", consequences={"fall"})
    payload = decision_to_payload(bridge(), 2, "uncertain", decision, ANA, finding)
    assert decision_from_payload(Q, _stored(payload)) == ("c10", MARA, 2, "uncertain", decision, ANA, finding)


@pytest.mark.parametrize("fields", [
    ("extrapolation", "approved", None, True, ()),  # an approval with no ruling
    ("extrapolation", "pending", "r1", False, ()),  # a ruling that decided nothing
    ("known_capability", "approved", "r1", False, ()),  # a declared use is never ruled on
    ("beyond_limits", "approved", "r1", True, ()),  # no ruling lifts a limit
    ("extrapolation", "approved", "r1", False, ()),  # an approved stretch is uncertain
    ("new_ability", "approved", "r1", True, (("light_bending", "x", "A price."),)),  # a new ability charges nothing
    ("feat", "not_needed", None, True, ()),
], ids=["approved-unruled", "pending-ruled", "declared-ruled", "limit-approved", "certain-stretch",
        "charged-new", "unknown"])
def test_a_finding_025_could_never_produce_is_refused(fields):
    with pytest.raises(AdjudicationError) as caught:
        AbilityFinding("light_bending", *fields)
    assert caught.value.reason == "invalid_field"


@pytest.mark.parametrize("change", [
    lambda p: p.update(extra=1), lambda p: p.update(revision=True), lambda p: p.update(cites=["b", "a"]),
    lambda p: p.update(actor="campaign:q-2/character/mara-1"), lambda p: p.update(result="glory"),
])
def test_a_tampered_decision_does_not_decode(change):
    payload = decision_to_payload(bridge(), 1, "uncertain", Decision("success", "adjudicated", {"acrobat", "gap"}),
                                  None, None)
    change(payload)
    with pytest.raises(AdjudicationError) as caught:
        decision_from_payload(Q, _stored(payload))
    assert caught.value.reason == "undecodable_entry"


# -- frame validation -------------------------------------------------------------------------

@pytest.mark.parametrize("build, reason", [
    (lambda: bridge(factors=(ACROBAT, ACROBAT)), "duplicate_factor"),
    (lambda: bridge(stakes=(FALL, Stake("fall", "Again."))), "duplicate_stake"),
    (lambda: ActionFrame("c10", MARA, ChatRef("q-2", "t-1"), moment(), "x", (), (), "l"), "campaign_mismatch"),
    (lambda: bridge(ability=ability(MARA, attempt_id="other")), "ability_mismatch"),
    (lambda: bridge(intent=" "), "invalid_field"),
    (lambda: ActionFrame("c10", InstanceRef(Q, "character", "mara-1"), CHAT, moment(), "x",
                         (Factor("a", "helps", "t", 1, "s"),), (), "l"), "invalid_field"),
])
def test_invalid_frames_are_refused(build, reason):
    with pytest.raises(AdjudicationError) as caught:
        build()
    assert caught.value.reason == reason
