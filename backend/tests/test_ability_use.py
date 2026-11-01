"""Deterministic assessment of labelled ability attempts (025): classification, rulings, failures."""

import dataclasses
import itertools
import pickle

import pytest
from ability_fixtures import (
    attempt, effective, flight, iris, light_bending, person, ruling, speak_with_dead, thruster_pack,
)

from grimoire.core import InstanceRef
from grimoire.core.abilities import Abilities, Ability, Cost, Effect, Limit, Prerequisite
from grimoire.core.ability_use import (
    AbilityAttempt, AbilityError, AbilityRuling, assess_attempt, check_ruling,
)
from grimoire.core.entity import EntityDefinition
from grimoire.core.facets import FacetSet, Physical
from grimoire.core.identity import TEXT_MAX, SourceRef


def refusal(build):
    with pytest.raises(AbilityError) as caught:
        build()
    assert isinstance(caught.value, ValueError)
    return caught.value.reason


def codes(assessment):
    return {reason.code for reason in assessment.reasons}


IRIS = effective(iris())
OPEN_IRIS = effective(iris(force_limit=False))


# --- the six classifications in isolation ------------------------------------------

def test_a_declared_reliable_effect_is_a_known_capability():
    result = assess_attempt(attempt(effect_id="veil"), IRIS)
    assert (result.classification, result.approval, result.uncertain) == ("known_capability", "not_needed", False)
    assert [cost.id for cost in result.costs] == ["eye_strain"]
    assert result.candidates == ("veil",) and "effect_covers" in codes(result)


def test_an_uncertain_effect_is_an_attempted_feat_with_its_costs():
    result = assess_attempt(attempt(needs=("light", "illusion"), effect_id="mirage"), IRIS)
    assert (result.classification, result.uncertain) == ("attempted_feat", True)
    assert [cost.id for cost in result.costs] == ["eye_strain", "migraine"]
    assert "effect_uncertain" in codes(result)


def test_no_declared_effect_and_no_limit_is_an_extrapolation_pending_approval():
    result = assess_attempt(flight(), OPEN_IRIS)
    assert (result.classification, result.approval, result.needs_approval) == ("extrapolation", "pending", True)
    assert result.costs == () and not result.uncertain and result.candidates == ()


def test_a_missing_ability_is_a_new_ability_pending_approval():
    result = assess_attempt(attempt(ability_id="pyrokinesis", needs=("fire",), conditions=()), IRIS)
    assert (result.classification, result.approval) == ("new_ability", "pending")
    assert result.costs == () and result.unknown_tags == ("fire",) and "no_ability" in codes(result)


def test_an_entity_without_the_facet_is_a_new_ability():
    # F10: no facet at all, and an empty facet, behave the same.
    for definition in (person("plain", "Plain"), person("blank", "Blank", facet_extras={})):
        result = assess_attempt(attempt(entity_id=definition.ref.entity_id, conditions=()), effective(definition))
        assert (result.classification, result.approval) == ("new_ability", "pending")


def test_a_limit_puts_an_attempt_beyond_limits():
    result = assess_attempt(flight(), IRIS)
    assert result.classification == "beyond_limits" and result.approval == "not_needed"
    assert result.conflicts == (("no_force", ("force",)),) and result.costs == ()


def test_an_unmet_prerequisite_makes_a_known_effect_unavailable():
    result = assess_attempt(attempt(effect_id="veil", conditions=()), IRIS)
    assert result.classification == "unavailable" and result.unmet_prerequisites == ("ambient_light",)
    assert result.costs == () and not result.uncertain


# --- B1, M2/M-a: labels ---------------------------------------------------------------

def test_omitting_or_misnaming_a_covering_effect_needs_a_relabel_not_an_approval():
    # F13: leaving out the effect must not turn a known power into an approvable extrapolation.
    for label in (None, "dazzle"):
        result = assess_attempt(attempt(effect_id=label), IRIS)
        assert (result.classification, result.approval) == ("relabel", "not_needed")
        assert result.candidates == ("veil",) and "covered_by_other_effect" in codes(result)
        with pytest.raises(AbilityError) as caught:
            check_ruling(ruling(attempt(effect_id=label)), IRIS)
        assert caught.value.reason == "ruling_not_needed"


def test_stale_references_never_raise():
    # F5: an id from the shared namespace that is not an effect or prerequisite, with the ability
    # present (relabel) or absent (new ability).
    for kwargs in ({"effect_id": "no_force"}, {"effect_id": "eye_strain"}, {"effect_id": "gone"},
                   {"conditions": ("ambient_light", "veil")}, {"conditions": ("ambient_light", "no_force")}):
        result = assess_attempt(attempt(**kwargs), IRIS)
        assert result.classification == "relabel" and result.stale_references
    result = assess_attempt(attempt(ability_id="gone", effect_id="veil"), IRIS)
    assert result.classification == "new_ability"
    assert result.stale_references == ("effect:veil", "prerequisite:ambient_light")
    assert {"stale_effect", "stale_prerequisite"} <= codes(result)


def test_combining_two_effects_tags_does_not_cover_an_attempt():
    # F9: veil gives concealment, mirage gives illusion; nothing gives both.
    result = assess_attempt(attempt(needs=("light", "concealment", "illusion")), IRIS)
    assert result.classification == "extrapolation" and result.candidates == ()


# --- precedence ---------------------------------------------------------------------

def test_precedence_between_findings_is_fixed():
    # beyond_limits over unavailable, over a stale reference, and with diagnostics still reported
    result = assess_attempt(flight(conditions=(), effect_id="gone"), IRIS)
    assert result.classification == "beyond_limits"
    assert result.unmet_prerequisites == ("ambient_light",) and result.stale_references == ("effect:gone",)
    # relabel (stale) over unavailable
    assert assess_attempt(attempt(effect_id="gone", conditions=()), IRIS).classification == "relabel"
    # unavailable over needs_upgrade
    upgraded = effective(person("iris", "Iris", light_bending(extras={"x-new": 1})))
    assert assess_attempt(attempt(effect_id="veil", conditions=()), upgraded).classification == "unavailable"
    # needs_upgrade over known and over relabel(candidates)
    assert assess_attempt(attempt(effect_id="veil"), upgraded).classification == "needs_upgrade"
    assert assess_attempt(attempt(), upgraded).classification == "needs_upgrade"
    # known over candidates
    assert assess_attempt(attempt(effect_id="veil"), IRIS).classification == "known_capability"
    # an absent ability with facet extras needs an upgrade, not an approval (M-d)
    facet_extras = effective(person("iris", "Iris", light_bending(), facet_extras={"x-limit": "no fire"}))
    result = assess_attempt(attempt(ability_id="pyrokinesis", needs=("fire",), conditions=()), facet_extras)
    assert (result.classification, result.approval, result.unrecognized) == ("needs_upgrade", "not_needed",
                                                                            ("#facet",))


# --- N2: unknown fields fail closed ------------------------------------------------------

def _bending_with(part):
    parts = {"effects": [Effect("veil", "t", {"light", "concealment"}), Effect("glow", "t", {"light", "glow"})],
             "limits": [Limit("no_force", "t", {"force"})], "costs": [Cost("eye_strain", "t")],
             "prerequisites": [Prerequisite("ambient_light", "t")]}
    kind, index, extras = part
    if kind == "ability":
        return Ability("light_bending", "Light bending", **parts, extras=extras)
    parts[kind][index] = dataclasses.replace(parts[kind][index], extras=extras)
    return Ability("light_bending", "Light bending", **parts)


@pytest.mark.parametrize("part, path", [
    (("ability", 0, {"x": 1}), "light_bending"),
    (("effects", 0, {"x": 1}), "light_bending/veil"),
    (("limits", 0, {"x": 1}), "light_bending/no_force"),
    (("costs", 0, {"x": 1}), "light_bending/eye_strain"),
    (("prerequisites", 0, {"x": 1}), "light_bending/ambient_light"),
])
def test_unknown_fields_on_the_named_ability_fail_closed(part, path):
    entity = effective(person("iris", "Iris", _bending_with(part)))
    result = assess_attempt(attempt(effect_id="veil"), entity)
    assert (result.classification, result.approval, result.costs) == ("needs_upgrade", "not_needed", ())
    assert result.unrecognized == (path,) and "unrecognized_constraint" in codes(result)
    with pytest.raises(AbilityError):
        check_ruling(ruling(attempt(effect_id="veil")), entity)


def test_unknown_fields_on_an_unrelated_effect_or_ability_do_not_matter():
    unrelated = _bending_with(("effects", 1, {"x": 1}))  # `glow`, not the named effect
    entity = effective(person("iris", "Iris", unrelated,
                              Ability("other", "Other", [Effect("e", "t", {"z"}, extras={"y": 2})])))
    result = assess_attempt(attempt(effect_id="veil"), entity)
    assert result.classification == "known_capability" and result.unrecognized == ()


# --- N4/M12: relabelling escapes are reported ---------------------------------------------

def test_relabelling_around_a_limit_is_reported():
    # F15: `push` is not `force`, so the limit does not bind, but the unused tag is reported.
    pushed = assess_attempt(attempt(needs=("light", "push", "flight")), IRIS)
    assert pushed.classification == "extrapolation" and pushed.unknown_tags == ("flight", "push")
    # Filing the flight under an invented ability is a new ability, with the real limit reported.
    invented = assess_attempt(attempt(ability_id="solar_sail", needs=("light", "force", "flight"),
                                      conditions=()), IRIS)
    assert invented.classification == "new_ability"
    assert invented.other_conflicts == (("light_bending", "no_force", ("force",)),)
    assert "other_limit_excludes" in codes(invented)


# --- rulings -------------------------------------------------------------------------------

def test_an_approval_lets_this_extrapolation_count_and_a_refusal_closes_it():
    approved = assess_attempt(flight(), OPEN_IRIS, [ruling(flight())])
    assert (approved.approval, approved.ruling_id, approved.uncertain) == ("approved", "r1", True)
    assert [cost.id for cost in approved.costs] == ["eye_strain"]
    refused = assess_attempt(flight(), OPEN_IRIS, [ruling(flight(), decision="refused")])
    assert (refused.approval, refused.costs, refused.uncertain) == ("refused", (), False)


def test_a_new_ability_approval_has_nothing_authored_to_charge():
    invented = attempt(ability_id="pyrokinesis", needs=("fire",), conditions=())
    result = assess_attempt(invented, IRIS, [ruling(invented, decides="new_ability")])
    assert (result.approval, result.uncertain, result.costs) == ("approved", True, ())


def test_a_ruling_on_an_altered_attempt_does_not_approve():
    # F7/F18: same attempt id, different label.
    relabelled = attempt(needs=("light", "force", "flight", "speed"))
    result = assess_attempt(relabelled, OPEN_IRIS, [ruling(flight())])
    assert result.approval == "pending" and result.mismatched_rulings == ("r1",)
    # The relabelled attempt can get a fresh ruling, and the old one stays visible.
    check_ruling(ruling(relabelled, ruling_id="r2"), OPEN_IRIS, [ruling(flight())])
    fresh = assess_attempt(relabelled, OPEN_IRIS, [ruling(flight()), ruling(relabelled, ruling_id="r2")])
    assert (fresh.approval, fresh.ruling_id, fresh.mismatched_rulings) == ("approved", "r2", ("r1",))


def test_a_ruling_survives_an_authored_change_without_crashing():
    # F14/F14b: approved as an extrapolation; the ability is then deleted.
    approval = ruling(flight())
    check_ruling(approval, OPEN_IRIS)
    gone = effective(person("iris", "Iris", speak_with_dead()))
    result = assess_attempt(flight(), gone, [approval])
    assert (result.classification, result.approval) == ("new_ability", "pending")
    assert result.inapplicable_rulings == ("r1",) and "inapplicable_ruling" in codes(result)
    # A refusal of the new classification is accepted and decides it.
    refusal_ = ruling(flight(), decides="new_ability", decision="refused", ruling_id="r2")
    check_ruling(refusal_, gone, [approval])
    decided = assess_attempt(flight(), gone, [approval, refusal_])
    assert (decided.approval, decided.ruling_id, decided.inapplicable_rulings) == ("refused", "r2", ("r1",))
    # A later authored limit makes both inapplicable; still no crash.
    limited = assess_attempt(flight(), IRIS, [approval, refusal_])
    assert limited.classification == "beyond_limits" and limited.inapplicable_rulings == ("r1", "r2")


def test_conflicting_rulings_are_refused_only_when_they_decide_this_attempt():
    # F6
    yes, no = ruling(flight()), ruling(flight(), decision="refused", ruling_id="r2")
    assert refusal(lambda: assess_attempt(flight(), OPEN_IRIS, [yes, no])) == "conflicting_rulings"
    assert refusal(lambda: check_ruling(no, OPEN_IRIS, [yes])) == "conflicting_rulings"
    # A conflict about another attempt is not this attempt's business.
    other = flight(attempt_id="t9-z")
    elsewhere = [ruling(other, ruling_id="r3"), ruling(other, decision="refused", ruling_id="r4")]
    assert assess_attempt(flight(), OPEN_IRIS, elsewhere).approval == "pending"
    # Two agreeing rulings are allowed; the lowest id is reported.
    agree = assess_attempt(flight(), OPEN_IRIS, [ruling(flight(), ruling_id="r9"), yes])
    assert (agree.approval, agree.ruling_id) == ("approved", "r1")


def test_ruling_ids_and_campaigns():
    # F6: duplicate ids in this campaign; another campaign's rulings are ignored, even on clashes.
    twin = ruling(flight(), decision="refused")
    assert refusal(lambda: assess_attempt(flight(), OPEN_IRIS, [ruling(flight()), twin])) == "duplicate_ruling"
    elsewhere = ruling(flight(campaign="c2"))
    result = assess_attempt(flight(), OPEN_IRIS, [ruling(flight()), elsewhere])
    assert (result.approval, result.mismatched_rulings) == ("approved", ())
    assert assess_attempt(flight(), OPEN_IRIS, [elsewhere]).approval == "pending"
    # Issuing a ruling under an id another attempt's ruling already uses.
    used = ruling(flight(attempt_id="t2"))  # id r1
    assert refusal(lambda: check_ruling(ruling(flight()), OPEN_IRIS, [used])) == "duplicate_ruling"


def test_check_ruling_refuses_rulings_that_cannot_decide_this_attempt():
    # F8, in its fixed order
    for decision in ("approved", "refused"):
        assert refusal(lambda: check_ruling(ruling(flight(), decision=decision), IRIS)) == "limit_not_approvable"
    for label in (attempt(effect_id="veil"), attempt(needs=("light", "illusion"), effect_id="mirage"),
                  attempt(), attempt(effect_id="veil", conditions=())):
        assert refusal(lambda: check_ruling(ruling(label), IRIS)) == "ruling_not_needed"
    assert refusal(lambda: check_ruling(ruling(flight(), decides="new_ability"), OPEN_IRIS)) == \
        "wrong_classification"
    check_ruling(ruling(flight()), OPEN_IRIS, [ruling(flight(), ruling_id="r0")])  # agreeing is fine
    with pytest.raises(TypeError):
        check_ruling(flight(), OPEN_IRIS)


# --- validation ----------------------------------------------------------------------------

def test_the_attempt_must_be_by_this_agent():
    # F4
    assert refusal(lambda: assess_attempt(attempt(entity_id="someone"), IRIS)) == "actor_mismatch"
    amulet = EntityDefinition(SourceRef("world:emberfall", "character", "amulet", "r1"), "Amulet",
                              "individual", FacetSet([Physical(), Abilities([light_bending()])]))
    assert refusal(lambda: assess_attempt(attempt(entity_id="amulet"), effective(amulet))) == "not_agent"
    with pytest.raises(TypeError):
        assess_attempt(attempt(), iris())
    with pytest.raises(TypeError):
        assess_attempt(ruling(attempt()), IRIS)


@pytest.mark.parametrize("build", [
    lambda: attempt(needs=()),
    lambda: AbilityAttempt("t1", InstanceRef("c1", "character", "iris-1"), "a", "light", "p"),
    lambda: attempt(needs=("Light",)),
    lambda: attempt(needs=[f"t{n}" for n in range(33)]),
    lambda: attempt(attempt_id=""),
    lambda: attempt(attempt_id="x" * (TEXT_MAX + 1)),
    lambda: attempt(ability_id="Light Bending"),
    lambda: attempt(effect_id=""),
    lambda: AbilityAttempt("t1", "campaign:c1/character/iris-1", "a", frozenset({"x"}), "p"),
    lambda: AbilityAttempt("t1", InstanceRef("c1", "character", "iris-1"), "a", frozenset({"x"}), " "),
    lambda: AbilityRuling("r1", attempt(), "known_capability", "approved", "director"),
    lambda: AbilityRuling("r1", attempt(), "extrapolation", "maybe", "director"),
    lambda: AbilityRuling("R 1", attempt(), "extrapolation", "approved", "director"),
    lambda: AbilityRuling("r1", "t1", "extrapolation", "approved", "director"),
    lambda: AbilityRuling("r1", attempt(), "extrapolation", "approved", ""),
    lambda: assess_attempt(attempt(), IRIS, ["r1"]),
    lambda: assess_attempt(attempt(), IRIS, ruling(attempt())),
    lambda: assess_attempt(attempt(), IRIS, [ruling(attempt(), ruling_id=f"r{n}") for n in range(257)]),
])
def test_malformed_inputs_are_refused(build):
    assert refusal(build) == "invalid_field"


def test_any_contribution_id_shaped_text_is_an_attempt_id():
    # F17
    for attempt_id in ("T4-a", "c/17", "turn 4: vault"):
        assert assess_attempt(attempt(attempt_id=attempt_id, effect_id="veil"), IRIS).attempt.attempt_id == attempt_id


def test_errors_pickle_with_their_reason():
    error = pickle.loads(pickle.dumps(AbilityError("x", "not_agent")))
    assert error.reason == "not_agent"


# --- determinism ---------------------------------------------------------------------------

def test_assessment_is_a_pure_function_of_its_inputs():
    # A8: permuted rulings and authored order give equal results; re-assessing is not a new grant.
    rulings = [ruling(flight(), ruling_id=f"r{n}") for n in range(3)] + [ruling(flight(attempt_id="t2"),
                                                                                ruling_id="r9")]
    expected = assess_attempt(flight(), OPEN_IRIS, rulings)
    for order in itertools.permutations(rulings):
        assert assess_attempt(flight(), OPEN_IRIS, order) == expected
    reordered = Ability("light_bending", "Light bending", list(reversed(light_bending(force_limit=False).effects)),
                        costs=list(reversed(light_bending().costs)),
                        prerequisites=light_bending().prerequisites,
                        description=light_bending().description)
    for reorder_abilities in itertools.permutations([reordered, speak_with_dead(), thruster_pack()]):
        entity = effective(person("iris", "Iris", *reorder_abilities))
        again = assess_attempt(flight(), entity, rulings)
        assert again.classification == expected.classification and again.costs == expected.costs
        assert again.reasons == assess_attempt(flight(), entity, list(reversed(rulings))).reasons
    assert assess_attempt(flight(), OPEN_IRIS, rulings) == expected
