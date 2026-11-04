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
    assert result.permitted
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
    assert result.classification == "beyond_limits" and result.approval == "not_approvable"
    assert not result.permitted and not result.needs_approval
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
        assert (result.classification, result.approval, result.costs) == ("relabel", "not_approvable", ())
        assert result.candidates == ("veil",) and "covered_by_other_effect" in codes(result)
        with pytest.raises(AbilityError) as caught:
            check_ruling(ruling(attempt(effect_id=label)), IRIS)
        assert caught.value.reason == "not_approvable"


def test_stale_references_never_raise():
    # F5: an id from the shared namespace that is not an effect or prerequisite, with the ability
    # present (relabel) or absent (new ability).
    for kwargs in ({"effect_id": "no_force"}, {"effect_id": "eye_strain"}, {"effect_id": "gone"},
                   {"conditions": ("ambient_light", "veil")}, {"conditions": ("ambient_light", "no_force")}):
        result = assess_attempt(attempt(**kwargs), IRIS)
        assert result.classification == "relabel" and result.stale_references
    # Naming an effect or prerequisite of a missing ability says it is not a new power: relabel.
    result = assess_attempt(attempt(ability_id="gone", needs=("fire",), effect_id="veil"), IRIS)
    assert (result.classification, result.approval) == ("relabel", "not_approvable")
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
    assert (result.classification, result.approval, result.unrecognized) == ("needs_upgrade", "not_approvable",
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
    assert (result.classification, result.approval, result.costs) == ("needs_upgrade", "not_approvable", ())
    assert result.unrecognized == (path,) and "unrecognized_constraint" in codes(result)
    assert refusal(lambda: check_ruling(ruling(attempt(effect_id="veil")), entity)) == "not_approvable"


def test_unknown_fields_on_any_effect_of_the_named_ability_fail_closed():
    # An extrapolation stretches the ability's effects without naming one, so an unknown field on
    # an effect it does not name could still narrow it: no approval may be reported over it.
    glow = _bending_with(("effects", 1, {"x-forbids": ["flight"]}))  # `glow`, not named
    entity = effective(person("iris", "Iris", glow))
    for label in (attempt(effect_id="veil"), attempt(needs=("light", "flight"))):
        result = assess_attempt(label, entity, [ruling(label)])
        assert (result.classification, result.approval, result.costs) == ("needs_upgrade", "not_approvable", ())
        assert result.unrecognized == ("light_bending/glow",)
        assert refusal(lambda: check_ruling(ruling(label, ruling_id="r2"), entity)) == "not_approvable"


def test_unknown_fields_on_another_ability_do_not_matter():
    entity = effective(person("iris", "Iris", _bending_with(("ability", 0, {})),
                              Ability("other", "Other", [Effect("e", "t", {"z"}, extras={"y": 2})])))
    result = assess_attempt(attempt(effect_id="veil"), entity)
    assert result.classification == "known_capability" and result.unrecognized == ()
    stretch = attempt(needs=("light", "flight"))
    assert assess_attempt(stretch, entity, [ruling(stretch)]).approval == "approved"


# --- N4/M12: relabelling escapes are reported ---------------------------------------------

def test_relabelling_around_a_limit_is_reported():
    # F15: `push` is not `force`, so the limit does not bind, but the unused tag is reported.
    pushed = assess_attempt(attempt(needs=("light", "push", "flight")), IRIS)
    assert pushed.classification == "extrapolation" and pushed.unknown_tags == ("flight", "push")
    # Filing the flight under an invented ability is a new ability, with the real limit reported.
    invented = assess_attempt(attempt(ability_id="solar_sail", needs=("light", "force", "flight"),
                                      conditions=()), IRIS)
    # It draws on light bending's `light`, so it is not a new power and must be filed there,
    # where `no_force` binds.
    assert (invented.classification, invented.other_drawn) == (
        "relabel", ("light_bending/dazzle", "light_bending/mirage", "light_bending/veil"))
    assert invented.other_conflicts == (("light_bending", "no_force", ("force",)),)
    assert "other_limit_excludes" in codes(invented)


# --- rulings -------------------------------------------------------------------------------

def test_an_approval_lets_this_extrapolation_count_and_a_refusal_closes_it():
    approved = assess_attempt(flight(), OPEN_IRIS, [ruling(flight())])
    assert (approved.approval, approved.ruling_id, approved.uncertain) == ("approved", "r1", True)
    assert [cost.id for cost in approved.costs] == ["eye_strain", "migraine"]
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
    glow = Ability("glow", "Glow", [Effect("shine", "t", {"glow"})])
    warm = attempt(ability_id="glow", needs=("glow", "warmth"), conditions=())
    before = effective(person("iris", "Iris", glow))
    approval = ruling(warm)
    check_ruling(approval, before)
    gone = effective(person("iris", "Iris", speak_with_dead()))
    result = assess_attempt(warm, gone, [approval])
    assert (result.classification, result.approval) == ("new_ability", "pending")
    assert result.inapplicable_rulings == ("r1",) and "inapplicable_ruling" in codes(result)
    # A refusal of the new classification is accepted and decides it.
    refusal_ = ruling(warm, decides="new_ability", decision="refused", ruling_id="r2")
    check_ruling(refusal_, gone, [approval])
    decided = assess_attempt(warm, gone, [approval, refusal_])
    assert (decided.approval, decided.ruling_id, decided.inapplicable_rulings) == ("refused", "r2", ("r1",))
    # A later authored limit makes both inapplicable; still no crash.
    limited_glow = Ability("glow", "Glow", [Effect("shine", "t", {"glow"})], [Limit("cold", "t", {"warmth"})])
    limited = assess_attempt(warm, effective(person("iris", "Iris", limited_glow)), [approval, refusal_])
    assert limited.classification == "beyond_limits" and limited.inapplicable_rulings == ("r1", "r2")
    # Deleting light bending under an approved flight extrapolation: its prerequisite is now stale.
    assert assess_attempt(flight(), gone, [ruling(flight())]).classification == "relabel"


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
    for label, expected in ((attempt(effect_id="veil"), "ruling_not_needed"),
                            (attempt(needs=("light", "illusion"), effect_id="mirage"), "ruling_not_needed"),
                            (attempt(), "not_approvable"),
                            (attempt(effect_id="veil", conditions=()), "not_approvable")):
        for decision in ("approved", "refused"):
            assert refusal(lambda: check_ruling(ruling(label, decision=decision), IRIS)) == expected
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
    lambda: attempt(effect_id=type("Loose", (str,), {})("veil")),
    lambda: AbilityAttempt("t1", type("Ref", (InstanceRef,), {})("c1", "character", "iris-1"), "a",
                           frozenset({"x"}), "p"),
])
def test_malformed_inputs_are_refused(build):
    assert refusal(build) == "invalid_field"


def test_the_ruling_bound_counts_only_rulings_on_this_attempt():
    # Many rulings elsewhere (another campaign, another attempt) are not this attempt's business.
    elsewhere = [ruling(flight(campaign="c2"), ruling_id=f"r{n}") for n in range(300)]
    others = [ruling(flight(attempt_id=f"t{n}"), ruling_id=f"q{n}") for n in range(300)]
    assert assess_attempt(flight(), OPEN_IRIS, elsewhere + others).approval == "pending"


def test_any_contribution_id_shaped_text_is_an_attempt_id():
    # F17
    for attempt_id in ("T4-a", "c/17", "turn 4: vault", "c" * (TEXT_MAX + 1)):
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
    baseline = assess_attempt(flight(), effective(person("iris", "Iris", light_bending(force_limit=False),
                                                         speak_with_dead(), thruster_pack())), rulings)
    # With the thruster pack present, flight draws on its `lift`: a cross-ability combination.
    assert (baseline.classification, baseline.other_drawn) == ("combination", ("thruster_pack/lift",))
    for reorder_abilities in itertools.permutations([reordered, speak_with_dead(), thruster_pack()]):
        entity = effective(person("iris", "Iris", *reorder_abilities))
        assert assess_attempt(flight(), entity, rulings) == baseline
        assert assess_attempt(flight(), entity, list(reversed(rulings))) == baseline
    assert assess_attempt(flight(), OPEN_IRIS, rulings) == expected


# --- effect-scoped prerequisites and costs (brutal review) -------------------------------

def _scoped_bending():
    return Ability("light_bending", "Light bending", [
        Effect("veil", "t", {"light", "concealment"}),
        Effect("mirage", "t", {"light", "illusion"}, certainty="uncertain"),
    ], costs=[Cost("eye_strain", "t"), Cost("migraine", "t", {"mirage"})],
        prerequisites=[Prerequisite("ambient_light", "t"), Prerequisite("still_air", "t", {"mirage"})])


SCOPED = effective(person("iris", "Iris", _scoped_bending()))


def test_an_effect_scoped_prerequisite_binds_its_effect_only():
    mirage = attempt(needs=("light", "illusion"), effect_id="mirage")
    blocked = assess_attempt(mirage, SCOPED)
    assert (blocked.classification, blocked.unmet_prerequisites) == ("unavailable", ("still_air",))
    calm = assess_attempt(attempt(needs=("light", "illusion"), effect_id="mirage",
                                  conditions=("ambient_light", "still_air")), SCOPED)
    assert calm.classification == "attempted_feat" and [c.id for c in calm.costs] == ["eye_strain", "migraine"]
    veil = assess_attempt(attempt(effect_id="veil"), SCOPED)
    assert veil.classification == "known_capability" and [c.id for c in veil.costs] == ["eye_strain"]


def test_leaving_out_the_effect_never_drops_a_stretched_effects_constraints():
    # A moving mirage is a stretch of `mirage`, whichever way it is labelled.
    for label in (None, "mirage", "veil"):
        moving = attempt(needs=("light", "illusion", "motion"), effect_id=label)
        result = assess_attempt(moving, SCOPED)
        assert (result.classification, result.unmet_prerequisites) == ("unavailable", ("still_air",))
        assert refusal(lambda: check_ruling(ruling(moving), SCOPED)) == "not_approvable"
    approved_label = attempt(needs=("light", "illusion", "motion"), conditions=("ambient_light", "still_air"))
    approved = assess_attempt(approved_label, SCOPED, [ruling(approved_label)])
    assert approved.approval == "approved" and [c.id for c in approved.costs] == ["eye_strain", "migraine"]
    # Every effect a stretch touches counts, even through a shared generic tag: deliberately
    # conservative, so a concealing stretch still needs the mirage's still air.
    hidden = attempt(needs=("light", "concealment", "motion"))
    assert assess_attempt(hidden, SCOPED).unmet_prerequisites == ("still_air",)


def test_issuing_the_ruling_past_the_bound_is_refused():
    existing = [ruling(flight(), ruling_id=f"r{n:03}") for n in range(256)]
    assert refusal(lambda: check_ruling(ruling(flight(), ruling_id="r999"), OPEN_IRIS, existing)) == \
        "too_many_rulings"


def test_a_duplicate_ruling_id_elsewhere_does_not_block_this_attempt():
    # Two devices reused an id on two other attempts; this attempt is unaffected.
    elsewhere = [ruling(flight(attempt_id="t3-a"), ruling_id="r7"), ruling(flight(attempt_id="t5-b"), ruling_id="r7")]
    assert assess_attempt(attempt(effect_id="veil", attempt_id="t9-z"), IRIS, elsewhere).permitted
    assert assess_attempt(flight(), OPEN_IRIS, elsewhere).approval == "pending"
    # Issuing a new ruling under an already used id is still refused.
    assert refusal(lambda: check_ruling(ruling(flight(), ruling_id="r7"), OPEN_IRIS, elsewhere)) == \
        "duplicate_ruling"


def test_provenance_is_attribution_not_part_of_what_a_ruling_decides():
    approval = ruling(flight())
    relabelled = AbilityAttempt(flight().attempt_id, flight().actor, "light_bending", flight().needs,
                                "labeller:v2", None, flight().conditions_met)
    result = assess_attempt(relabelled, OPEN_IRIS, [approval])
    assert (result.approval, result.mismatched_rulings) == ("approved", ())


def test_unknown_fields_on_other_abilities_are_reported():
    other = Ability("solar_sail", "Solar sail", [Effect("drift", "t", {"drift"})], extras={"x-forbids": ["flight"]})
    entity = effective(person("iris", "Iris", light_bending(force_limit=False), other))
    result = assess_attempt(attempt(ability_id="pyrokinesis", needs=("fire",), conditions=()), entity)
    assert result.classification == "new_ability" and result.other_unrecognized == ("solar_sail",)
    assert "other_unrecognized_constraint" in codes(result)
    assert assess_attempt(flight(), entity).other_unrecognized == ("solar_sail",)
    with pytest.raises(TypeError):
        sub = type("Sub", (type(entity),), {})
        assess_attempt(flight(), sub(**{f.name: getattr(entity, f.name) for f in dataclasses.fields(entity)}))


# --- round 2 of independent review --------------------------------------------------------

def _wide_veil():
    return Ability("light_bending", "Light bending", [
        Effect("veil", "t", {"light", "concealment", "body"}),
        Effect("mirage", "t", {"light", "illusion"}, certainty="uncertain"),
    ], costs=[Cost("eye_strain", "t"), Cost("migraine", "t", {"mirage"})],
        prerequisites=[Prerequisite("ambient_light", "t"), Prerequisite("still_air", "t", {"mirage"})])


def test_a_stretch_drawing_on_a_smaller_effect_keeps_its_constraints():
    # Hiding inside a mirage of herself: `veil` shares more tags, but only `mirage` gives illusion.
    entity = effective(person("iris", "Iris", _wide_veil()))
    for label in (None, "veil", "mirage"):
        decoy = attempt(needs=("light", "concealment", "body", "illusion"), effect_id=label)
        result = assess_attempt(decoy, entity)
        assert (result.classification, result.unmet_prerequisites) == ("unavailable", ("still_air",))
    calm = attempt(needs=("light", "concealment", "body", "illusion"), conditions=("ambient_light", "still_air"))
    approved = assess_attempt(calm, entity, [ruling(calm)])
    assert approved.permitted and [c.id for c in approved.costs] == ["eye_strain", "migraine"]


def test_adding_a_needed_tag_never_removes_a_scoped_constraint():
    # Property: for every stretch over the authored vocabulary, a superset of needs keeps every
    # unmet prerequisite (unless a limit or a new-ability classification takes over) and cost.
    entity = effective(person("iris", "Iris", _wide_veil()))
    vocabulary = ["light", "concealment", "body", "illusion", "motion", "glow"]
    for size in range(1, 4):
        for needs in itertools.combinations(vocabulary, size):
            base = attempt(needs=needs)
            before = assess_attempt(base, entity, [ruling(base)])
            for extra in vocabulary:
                if extra in needs:
                    continue
                more = attempt(needs=(*needs, extra))
                after = assess_attempt(more, entity, [ruling(more)])
                if after.classification in ("unavailable", "extrapolation") and \
                        before.classification in ("unavailable", "extrapolation"):
                    assert set(before.unmet_prerequisites) <= set(after.unmet_prerequisites)
                    if before.costs and after.costs:
                        assert set(before.costs) <= set(after.costs)


def test_an_attempt_sharing_no_tag_with_the_ability_must_be_filed_under_its_own_id():
    # Not a use of light bending, so neither an extrapolation of it nor an approvable new power
    # that would skip its "every use" prerequisites and costs.
    for label in (attempt(needs=("telekinesis",), conditions=()),
                  attempt(needs=("telekinesis",), effect_id="veil", conditions=())):
        result = assess_attempt(label, IRIS, [ruling(label, decides="new_ability")])
        assert (result.classification, result.approval, result.permitted) == ("relabel", "not_approvable", False)
        assert "outside_ability" in codes(result)
        assert refusal(lambda: check_ruling(ruling(label, decides="new_ability", ruling_id="r2"), IRIS)) == \
            "not_approvable"
    # Filed under its own id it is a genuine new ability.
    own = attempt(ability_id="telekinesis", needs=("telekinesis",), conditions=())
    assert assess_attempt(own, IRIS).classification == "new_ability"
    check_ruling(ruling(own, decides="new_ability"), IRIS)
    # The named ability's limits still bind.
    assert assess_attempt(attempt(needs=("force",), conditions=()), IRIS).classification == "beyond_limits"


@pytest.mark.parametrize("change", [
    {"effect_id": "veil"}, {"effect_id": "mirage"}, {"conditions": ()}, {"ability_id": "light_bending_2"},
])
def test_every_label_field_is_part_of_what_a_ruling_decides(change):
    approval = ruling(flight())
    relabelled = flight(**change)
    result = assess_attempt(relabelled, OPEN_IRIS, [approval])
    assert (result.approval, result.mismatched_rulings) in (("pending", ("r1",)), ("not_approvable", ("r1",)))


def test_another_actors_ruling_under_the_same_attempt_id_is_ignored():
    other = AbilityAttempt("t1-a", InstanceRef("c1", "character", "bob-1"), "light_bending",
                           frozenset({"light", "force", "flight"}), "scene:s1/t1")
    result = assess_attempt(flight(), OPEN_IRIS, [AbilityRuling("r1", other, "extrapolation", "approved", "d")])
    assert (result.approval, result.mismatched_rulings) == ("pending", ())


def test_the_same_ruling_record_seen_twice_is_one_ruling():
    approval = ruling(flight())
    assert assess_attempt(flight(), OPEN_IRIS, [approval, approval]).approval == "approved"
    twin = ruling(flight(), decision="refused")  # same id, different content
    assert refusal(lambda: assess_attempt(flight(), OPEN_IRIS, [approval, twin])) == "duplicate_ruling"


def test_assessment_follows_the_labels_it_is_given():
    # Under-labelling escape (documented trust boundary): leaving the outcome tags out of `needs`
    # makes radiation-pressure flight read as a plain veil. The labeller must state every outcome.
    result = assess_attempt(attempt(needs=("light",), effect_id="veil"), IRIS)
    assert result.classification == "known_capability" and result.conflicts == ()
    honest = assess_attempt(flight(effect_id="veil"), IRIS)
    assert honest.classification == "beyond_limits"


def test_without_the_facet_every_needed_tag_is_unknown():
    result = assess_attempt(attempt(entity_id="plain", needs=("fire", "light"), conditions=()),
                            effective(person("plain", "Plain")))
    assert result.unknown_tags == ("fire", "light")


# --- round 3 of independent review --------------------------------------------------------

VANTA = effective(person("vanta", "Vanta", light_bending(), thruster_pack()))


def test_a_power_filed_under_the_wrong_ability_id_needs_a_relabel():
    # An invented id, or a sibling ability that shares no tag: another ability's declared effect
    # covers it, so it must not become an approvable new ability that skips that effect's
    # prerequisites and costs.
    for ability_id in ("shadow_veil", "thruster_pack"):
        hidden = attempt(entity_id="vanta", ability_id=ability_id, needs=("light", "concealment"), conditions=())
        result = assess_attempt(hidden, VANTA, [ruling(hidden, decides="new_ability")])
        assert (result.classification, result.permitted, result.other_candidates) == \
            ("relabel", False, ("light_bending/veil",))
        assert "covered_by_other_ability" in codes(result)
    honest = assess_attempt(attempt(entity_id="vanta", effect_id="veil", conditions=()), VANTA)
    assert honest.classification == "unavailable"


def test_check_ruling_ignores_ruling_ids_used_in_another_campaign():
    check_ruling(ruling(flight()), OPEN_IRIS, [ruling(flight(campaign="c2"))])


def test_a_ruling_note_must_be_text():
    assert refusal(lambda: AbilityRuling("r1", flight(), "extrapolation", "approved", "d", note=3)) == \
        "invalid_field"
    assert refusal(lambda: AbilityRuling("r1", flight(), "extrapolation", "approved", "d", note="x" * 513)) == \
        "invalid_field"


# --- round 4 of independent review --------------------------------------------------------

def test_an_attempt_drawing_on_another_ability_is_never_approvable():
    # The proving scenario filed under the thruster pack: light bending's `no_force` must not be
    # escaped by filing the flight under a sibling that shares a tag.
    both = effective(person("iris", "Iris", light_bending(), thruster_pack()))
    radiant = attempt(ability_id="thruster_pack", needs=("light", "force", "flight"), conditions=("fuel",))
    result = assess_attempt(radiant, both, [ruling(radiant)])
    # The stretch draws on light bending (`light`), whose `no_force` limit binds it.
    assert (result.classification, result.permitted) == ("beyond_limits", False)
    assert "draws_on_other_ability" in codes(result)
    assert refusal(lambda: check_ruling(ruling(radiant, ruling_id="r2"), both)) == "limit_not_approvable"
    # A partial stretch of light bending filed under the thruster pack, in the dark.
    dark = attempt(ability_id="thruster_pack", needs=("light", "flight"), conditions=("fuel",))
    assert assess_attempt(dark, both).classification == "combination"
    honest = attempt(ability_id="light_bending", needs=("light", "flight"), conditions=())
    assert assess_attempt(honest, both).classification == "combination"  # draws on `lift` for flight
    assert refusal(lambda: check_ruling(ruling(honest), both)) == "not_approvable"
    # A partial stretch filed under an invented id.
    hidden = attempt(ability_id="light_bending_2", needs=("light", "concealment", "motion"), conditions=())
    assert assess_attempt(hidden, IRIS).classification == "relabel"


def test_a_covered_power_filed_under_a_sibling_sharing_a_tag_needs_a_relabel():
    sunwings = Ability("sunwings", "Sunwings", [Effect("soar", "t", {"light", "flight"})],
                       costs=[Cost("exhaustion", "t")], prerequisites=[Prerequisite("daylight", "t")])
    entity = effective(person("iris", "Iris", light_bending(force_limit=False), sunwings))
    night = attempt(needs=("light", "flight"), conditions=("ambient_light",))
    result = assess_attempt(night, entity, [ruling(night)])
    assert (result.classification, result.other_candidates) == ("relabel", ("sunwings/soar",))
    honest = attempt(ability_id="sunwings", needs=("light", "flight"), effect_id="soar", conditions=())
    assert assess_attempt(honest, entity).classification == "unavailable"


def test_a_power_using_only_its_own_tags_is_unaffected_by_siblings():
    # A known use, and a stretch of only the named ability's own vocabulary, stay as they were.
    both = effective(person("iris", "Iris", light_bending(), thruster_pack()))
    assert assess_attempt(attempt(effect_id="veil"), both).classification == "known_capability"
    stretch = attempt(needs=("light", "concealment", "illusion"))
    assert assess_attempt(stretch, both).classification == "extrapolation"
    hop = attempt(ability_id="thruster_pack", needs=("thrust", "flight"), effect_id="lift", conditions=("fuel",))
    assert assess_attempt(hop, both).classification == "known_capability"


def test_no_facet_and_an_empty_facet_report_the_same_reasons():
    plain = assess_attempt(attempt(entity_id="plain", needs=("fire",), conditions=()), effective(person("plain", "P")))
    blank = assess_attempt(attempt(entity_id="blank", needs=("fire",), conditions=()),
                           effective(person("blank", "B", facet_extras={})))
    assert [r.code for r in plain.reasons] == [r.code for r in blank.reasons]
    assert plain.unknown_tags == blank.unknown_tags == ("fire",)


# --- round 5 of independent review --------------------------------------------------------

@pytest.mark.parametrize("first, second, needs", [
    # A narrow power and a broad one covering it.
    (Ability("cantrips", "Cantrips", [Effect("spark", "t", {"fire"})]),
     Ability("firebolt", "Firebolt", [Effect("bolt", "t", {"fire", "projectile"})]), ("fire",)),
    # Two powers with the same tags.
    (Ability("wings", "Wings", [Effect("fly", "t", {"flight"})]),
     Ability("jetpack", "Jetpack", [Effect("hop", "t", {"flight"})]), ("flight",)),
])
def test_overlapping_abilities_never_deadlock_a_declared_use(first, second, needs):
    entity = effective(person("iris", "Iris", first, second))
    for own, other in ((first, second), (second, first)):
        use = attempt(ability_id=own.id, needs=needs, effect_id=own.effects[0].id, conditions=())
        result = assess_attempt(use, entity)
        assert (result.classification, result.permitted) == ("known_capability", True)
        assert result.other_candidates == (f"{other.id}/{other.effects[0].id}",)  # still advisory
    # Naming no effect still needs a relabel to the named ability's own covering effect.
    vague = attempt(ability_id=first.id, needs=needs, conditions=())
    assert assess_attempt(vague, entity).candidates == (first.effects[0].id,)
    assert assess_attempt(vague, entity).classification == "relabel"


def test_an_overlapping_sibling_does_not_bypass_the_named_effects_constraints():
    telepathy = Ability("telepathy", "Telepathy", [Effect("mindspeak", "t", {"speech"})],
                        costs=[Cost("headache", "t")], prerequisites=[Prerequisite("line_of_sight", "t")])
    sefa = effective(person("sefa", "Sefa", speak_with_dead(), telepathy))
    blind = assess_attempt(attempt("sefa", "telepathy", ("speech",), effect_id="mindspeak", conditions=()), sefa)
    assert blind.classification == "unavailable" and blind.unmet_prerequisites == ("line_of_sight",)
    seen = assess_attempt(attempt("sefa", "telepathy", ("speech",), effect_id="mindspeak",
                                  conditions=("line_of_sight",)), sefa)
    assert seen.classification == "known_capability" and [c.id for c in seen.costs] == ["headache"]


# --- round 6 of independent review --------------------------------------------------------

SUNLANCE = Ability("sunlance", "Sunlance", [Effect("lance", "t", {"light", "heat"})])


def test_a_stretch_cannot_escape_a_sibling_sharing_its_mechanism_tag():
    # The proving scenario filed under another light-based power: light bending's `no_force`,
    # `ambient_light` and `eye_strain` still bind, because the flight draws on its `light`.
    entity = effective(person("iris", "Iris", light_bending(), SUNLANCE))
    dark = attempt(ability_id="sunlance", needs=("light", "force", "flight"), conditions=())
    result = assess_attempt(dark, entity, [ruling(dark)])
    assert (result.classification, result.permitted) == ("beyond_limits", False)
    assert refusal(lambda: check_ruling(ruling(dark, ruling_id="r2"), entity)) == "limit_not_approvable"
    # Without the limit it is a combination of the two abilities: never approvable in this slice.
    open_entity = effective(person("iris", "Iris", light_bending(force_limit=False), SUNLANCE))
    combo = assess_attempt(dark, open_entity, [ruling(dark)])
    assert (combo.classification, combo.approval) == ("combination", "not_approvable")
    # A declared use of either power is untouched by the other.
    lance = attempt(ability_id="sunlance", needs=("light", "heat"), effect_id="lance", conditions=())
    assert assess_attempt(lance, entity).classification == "known_capability"


def test_an_attempt_outside_the_named_ability_is_relabelled_before_its_limits():
    telekinesis = Ability("telekinesis", "Telekinesis", [Effect("push", "t", {"force", "telekinesis"})])
    entity = effective(person("iris", "Iris", light_bending(), telekinesis))
    misfiled = assess_attempt(attempt(needs=("force", "telekinesis"), conditions=()), entity)
    assert (misfiled.classification, misfiled.other_candidates) == ("relabel", ("telekinesis/push",))
    filed = attempt(ability_id="telekinesis", needs=("force", "telekinesis"), effect_id="push", conditions=())
    assert assess_attempt(filed, entity).classification == "known_capability"


def test_a_sibling_covering_what_the_named_ability_only_stretches_needs_a_relabel():
    bending = Ability("light_bending", "Light bending", [Effect("veil", "t", {"light", "concealment"}),
                                                         Effect("dazzle", "t", {"light", "glare"})])
    cloak = Ability("flash_cloak", "Flash cloak", [Effect("cloak", "t", {"concealment", "glare"})],
                    costs=[Cost("burnout", "t")], prerequisites=[Prerequisite("darkness", "t")])
    entity = effective(person("iris", "Iris", bending, cloak))
    misfiled = attempt(needs=("concealment", "glare"), conditions=())
    result = assess_attempt(misfiled, entity, [ruling(misfiled)])
    assert (result.classification, result.other_candidates) == ("relabel", ("flash_cloak/cloak",))
    honest = attempt(ability_id="flash_cloak", needs=("concealment", "glare"), effect_id="cloak", conditions=())
    assert assess_attempt(honest, entity).classification == "unavailable"


def test_a_named_effect_stretch_pays_every_touched_effects_costs():
    stretch = attempt(needs=("light", "concealment", "flight"), effect_id="veil")
    approved = assess_attempt(stretch, OPEN_IRIS, [ruling(stretch)])
    assert approved.permitted and [c.id for c in approved.costs] == ["eye_strain", "migraine"]


def test_a_missing_ability_drawing_on_one_or_several_abilities():
    entity = effective(person("iris", "Iris", light_bending(force_limit=False), thruster_pack()))
    one = attempt(ability_id="solar_sail", needs=("light", "drift"), conditions=())
    assert assess_attempt(one, entity).classification == "relabel"      # file it under light bending
    two = attempt(ability_id="solar_sail", needs=("light", "flight"), conditions=())
    assert assess_attempt(two, entity).classification == "combination"  # light bending and the pack
