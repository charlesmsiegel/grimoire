"""Sprint 025 proving scenarios: constrained abilities adjudicated without dice or invented powers."""

import pytest
from ability_fixtures import (
    CAMPAIGN, EMBER, actor, attempt, effective, flight, index_of, iris, light_bending, person, ruling,
    speak_with_dead, thruster_pack,
)
from store_helpers import OWNER, new_library, owner_handle

from grimoire.core import InstanceRef, SourceRef, assess_attempt, check_ruling
from grimoire.core.abilities import Abilities
from grimoire.core.ability_use import AbilityError
from grimoire.core.entity import EntityInstance, effective_entity
from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_from_payload, definition_to_payload
from grimoire.core.facets import Agent, FacetBundle
from grimoire.store import RecordEnvelope, SourceManifest


def test_a1_flight_by_radiation_pressure_is_beyond_her_limits():
    result = assess_attempt(flight(), effective(iris()))
    assert result.classification == "beyond_limits"
    assert result.conflicts == (("no_force", ("force",)),)
    assert (result.approval, result.costs, result.uncertain, result.permitted) == ("not_approvable", (), False, False)
    assert result.other_conflicts == () and "force" not in result.unknown_tags
    assert "limit_excludes" in {reason.code for reason in result.reasons}
    # Nothing about it is mechanical: no roll, no ruleset, and no ruling can grant it.
    with pytest.raises(AbilityError) as caught:
        check_ruling(ruling(flight()), effective(iris()))
    assert caught.value.reason == "limit_not_approvable"


def test_a2_without_the_limit_flight_is_an_extrapolation_that_waits_for_a_ruling():
    entity = effective(iris(force_limit=False))
    pending = assess_attempt(flight(), entity)
    assert (pending.classification, pending.approval, pending.costs) == ("extrapolation", "pending", ())
    approval = ruling(flight())
    check_ruling(approval, entity)
    approved = assess_attempt(flight(), entity, [approval])
    assert (approved.approval, approved.uncertain) == ("approved", True)
    # Flight touches every effect through `light`, so all their scoped costs apply too: a
    # stretch never pays less than the effects it draws on.
    assert [cost.id for cost in approved.costs] == ["eye_strain", "migraine"]
    refused = assess_attempt(flight(), entity, [ruling(flight(), decision="refused")])
    assert (refused.approval, refused.costs) == ("refused", ())


def test_a3_veil_needs_light():
    entity = effective(iris())
    assert assess_attempt(attempt(effect_id="veil"), entity).classification == "known_capability"
    dark = assess_attempt(attempt(effect_id="veil", conditions=()), entity)
    assert (dark.classification, dark.costs) == ("unavailable", ())


def test_a4_a_mirage_is_an_attempted_feat_that_costs_a_migraine():
    result = assess_attempt(attempt(needs=("light", "illusion"), effect_id="mirage"), effective(iris()))
    assert (result.classification, result.uncertain) == ("attempted_feat", True)
    assert [cost.id for cost in result.costs] == ["eye_strain", "migraine"]


def test_a5_speaking_with_the_dead():
    entity = effective(person("sefa", "Sefa", speak_with_dead()))
    def go(needs, effect_id=None, conditions=("corpse_present",)):
        return assess_attempt(attempt("sefa", "speak_with_dead", needs, effect_id=effect_id,
                                      conditions=conditions), entity)
    assert go(("necromancy", "sense"), "sense_death").classification == "known_capability"
    asked = go(("necromancy", "speech"), "question_corpse")
    assert asked.classification == "attempted_feat" and [c.id for c in asked.costs] == ["lingering_chill"]
    assert go(("necromancy", "speech"), "question_corpse", conditions=()).classification == "unavailable"
    ancient = go(("necromancy", "speech", "ancient_dead"))
    assert ancient.classification == "beyond_limits" and ancient.conflicts == (("recent_dead_only",
                                                                                 ("ancient_dead",)),)
    raise_dead = go(("necromancy", "reanimation"))
    assert (raise_dead.classification, raise_dead.approval) == ("extrapolation", "pending")


def test_a5_a_thruster_pack():
    entity = effective(person("orin", "Orin", thruster_pack()))
    def go(needs, effect_id=None, conditions=("fuel",)):
        return assess_attempt(attempt("orin", "thruster_pack", needs, effect_id=effect_id,
                                      conditions=conditions), entity)
    hop = go(("thrust", "flight"), "lift")
    assert hop.classification == "known_capability" and [c.id for c in hop.costs] == ["cooldown", "fuel_burn"]
    assert go(("thrust", "glide"), "glide").classification == "attempted_feat"
    assert go(("thrust", "flight"), "lift", conditions=()).classification == "unavailable"
    assert go(("thrust", "flight", "silence")).classification == "beyond_limits"
    orbit = go(("thrust", "flight", "orbit"))
    assert (orbit.classification, orbit.approval) == ("extrapolation", "pending")


def test_a6_abilities_change_per_campaign_instance_not_in_the_definition():
    base = iris()
    index = index_of(base)
    # A campaign-original actor with its own power.
    orin = EntityInstance(InstanceRef(CAMPAIGN, "character", "orin-1"), None, "Orin",
                          added=FacetBundle([Agent(), Abilities([thruster_pack()])]))
    hop = attempt("orin", "thruster_pack", ("thrust", "flight"), effect_id="lift", conditions=("fuel",))
    assert assess_attempt(hop, effective_entity(orin, index)).classification == "known_capability"
    # Campaign c1 lifts the force limit for its Iris (an authored replacement); c2's Iris is unchanged.
    evolved = EntityInstance(actor("iris"), base.ref,
                             replaced=FacetBundle([Abilities([light_bending(force_limit=False)])]))
    untouched = EntityInstance(actor("iris", "c2"), base.ref)
    assert assess_attempt(flight(), effective_entity(evolved, index)).classification == "extrapolation"
    assert assess_attempt(flight(campaign="c2"), effective_entity(untouched, index)).classification == \
        "beyond_limits"
    assert base.traits.get(Abilities).by_id("light_bending").limits  # the definition kept its limit


def test_a6_pinning_and_the_whole_facet_copy_limitation():
    r1 = iris()
    r2_abilities = Abilities([light_bending(), speak_with_dead()])
    r2 = person("iris", "Iris", *r2_abilities.abilities, revision="r2")
    index = index_of(r1, r2, heads={SourceRef(EMBER, "character", "iris"): "r2"})
    follower = EntityInstance(actor("iris"), SourceRef(EMBER, "character", "iris"))
    pinned = EntityInstance(actor("iris", "c2"), r1.ref)
    talk = attempt(ability_id="speak_with_dead", needs=("necromancy", "sense"), effect_id="sense_death",
                   conditions=("corpse_present",))
    assert assess_attempt(talk, effective_entity(follower, index)).classification == "known_capability"
    pinned_talk = attempt(ability_id="speak_with_dead", needs=("necromancy", "sense"), conditions=(),
                          campaign="c2")
    assert assess_attempt(pinned_talk, effective_entity(pinned, index)).classification == "new_ability"
    # Replacing the facet to evolve one ability copies all of them, so the copy stops following the head:
    # the head's new ability is not on this instance.
    copied = EntityInstance(actor("iris"), SourceRef(EMBER, "character", "iris"),
                            replaced=FacetBundle([Abilities([light_bending(force_limit=False)])]))
    result = assess_attempt(talk, effective_entity(copied, index))
    assert result.classification == "relabel" and result.stale_references == (
        "effect:sense_death", "prerequisite:corpse_present")


def test_a7_a_definition_with_abilities_survives_the_native_store(tmp_path):
    definition = person("vanta", "Vanta", light_bending(), speak_with_dead(), thruster_pack())
    store = new_library(tmp_path / "library")
    txn = store.transaction()
    txn.put(SourceManifest(EMBER, OWNER, "Emberfall", {}, ()), None)
    txn.put(RecordEnvelope(definition.ref, definition_to_payload(definition), name="Vanta",
                           extra={"schema": ENTITY_SCHEMA}), None)
    txn.commit(owner_handle())
    stored = store.load().index.resolve(definition.ref)
    decoded = definition_from_payload(definition.ref, stored.envelope.extra["schema"], stored.envelope.payload)
    assert decoded == definition
    entity = effective(decoded)
    hop = attempt("vanta", "thruster_pack", ("thrust", "flight"), effect_id="lift", conditions=("fuel",))
    assert assess_attempt(hop, entity).classification == "known_capability"
    # With the thruster pack present, flight also draws on it: a combination in which light
    # bending's `no_force` binds unless a director's ruling sets it aside by name.
    combined = assess_attempt(flight(entity_id="vanta"), entity)
    assert (combined.classification, combined.binding_limits) == ("combination", ("light_bending/no_force",))


def test_a8_the_ability_modules_load_no_provider_or_store():
    import os
    import subprocess
    import sys

    from purity import SRC
    script = ("import sys\nimport grimoire.core.ability_use, grimoire.core.abilities\n"
              "print('\\n'.join(sys.modules))\n")
    done = subprocess.run([sys.executable, "-S", "-c", script], capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(SRC)}, check=True)
    loaded = set(done.stdout.split())
    assert not {m for m in loaded if m.split(".")[:2] in (["grimoire", "providers"], ["grimoire", "store"],
                                                          ["grimoire", "capture"])}
    assert "grimoire.core.ability_use" in loaded  # positive control


def test_a6_a_head_gaining_the_facet_breaks_an_instance_that_added_one():
    # The whole-facet limitation, through 009's source-drift rule: once the definition's head has
    # an `abilities` trait, an unpinned instance that *added* its own facet no longer resolves.
    import pytest
    from grimoire.core.entity import EntityError
    r1 = person("iris", "Iris")
    r2 = person("iris", "Iris", speak_with_dead(), revision="r2")
    added = EntityInstance(actor("iris"), SourceRef(EMBER, "character", "iris"),
                           added=FacetBundle([Abilities([light_bending()])]))
    assert effective_entity(added, index_of(r1, heads={SourceRef(EMBER, "character", "iris"): "r1"}))
    with pytest.raises(EntityError) as caught:
        effective_entity(added, index_of(r1, r2, heads={SourceRef(EMBER, "character", "iris"): "r2"}))
    assert caught.value.reason == "added_shadows_trait"
