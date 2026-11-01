"""Synthetic abilities for the sprint 025 scenarios (no user content).

Three constrained, mechanics-free abilities: light bending, speaking with the
dead and a thruster pack. Every prerequisite is ability-wide (empty
``applies_to``), so an attempt must say it is met.
"""

from grimoire.core import DefinitionIndex, InstanceRef, SourceDescriptor, SourceRef
from grimoire.core.abilities import Abilities, Ability, Cost, Effect, Limit, Prerequisite
from grimoire.core.ability_use import AbilityAttempt, AbilityRuling
from grimoire.core.entity import EntityDefinition, EntityInstance, effective_entity
from grimoire.core.facets import Agent, Biological, FacetSet, Physical

EMBER = "world:emberfall"
CAMPAIGN = "c1"


def light_bending(*, force_limit=True, extras=None):
    limits = [Limit("no_force", "Light carries no meaningful push; she cannot move or hold matter with it.",
                    {"force"})] if force_limit else []
    return Ability(
        "light_bending", "Light bending",
        effects=[
            Effect("veil", "Bends light around her body so she cannot be seen.", {"light", "concealment"},
                   range="her body and an arm's length"),
            Effect("dazzle", "A sudden glare in someone's eyes.", {"light", "glare"}, certainty="uncertain"),
            Effect("mirage", "A still image of something that is not there.", {"light", "illusion"},
                   range="line of sight", certainty="uncertain"),
        ],
        limits=limits,
        costs=[Cost("eye_strain", "Every use leaves her eyes aching."),
               Cost("migraine", "Holding a mirage gives her a splitting headache.", {"mirage"})],
        prerequisites=[Prerequisite("ambient_light", "There must be light to bend.")],
        description="She shapes light that is already present; she cannot make it.",
        extras=extras or {},
    )


def speak_with_dead():
    return Ability(
        "speak_with_dead", "Speak with the dead",
        effects=[
            Effect("sense_death", "Knows whether someone near died, and roughly when.", {"necromancy", "sense"}),
            Effect("question_corpse", "Asks a corpse a few questions; it answers as it would have in life.",
                   {"necromancy", "speech"}, certainty="uncertain"),
        ],
        limits=[Limit("recent_dead_only", "Only the dead of the last few days answer.", {"ancient_dead"})],
        costs=[Cost("lingering_chill", "A chill that stays for an hour.", {"question_corpse"})],
        prerequisites=[Prerequisite("corpse_present", "The body must be within reach.")],
    )


def thruster_pack():
    return Ability(
        "thruster_pack", "Thruster pack",
        effects=[
            Effect("lift", "A short powered hop.", {"thrust", "flight"}, range="a short hop"),
            Effect("glide", "Slows a fall into a long glide.", {"thrust", "glide"}, certainty="uncertain"),
        ],
        limits=[Limit("loud", "The thrusters roar; nothing about it is quiet.", {"silence"})],
        costs=[Cost("fuel_burn", "Each burn uses fuel."),
               Cost("cooldown", "The nozzles must cool for a few minutes.")],
        prerequisites=[Prerequisite("fuel", "The tank must hold fuel.")],
    )


def ref(entity_id, revision="r1", kind="character"):
    return SourceRef(EMBER, kind, entity_id, revision)


def person(entity_id, name, *abilities, revision="r1", facet_extras=None):
    traits = [Physical(), Biological(), Agent()]
    if abilities or facet_extras is not None:
        traits.append(Abilities(abilities, facet_extras or {}))
    return EntityDefinition(ref(entity_id, revision), name, "individual", FacetSet(traits))


def iris(**kwargs):
    return person("iris", "Iris", light_bending(**kwargs))


def index_of(*definitions, heads=None):
    sources = sorted({definition.ref.source for definition in definitions})
    return DefinitionIndex([SourceDescriptor(source, owner="user") for source in sources],
                           definitions, heads)


def actor(entity_id, campaign=CAMPAIGN):
    return InstanceRef(campaign, "character", f"{entity_id}-1")


def effective(definition, campaign=CAMPAIGN, **instance_kwargs):
    """The effective entity of one instance of ``definition`` (pinned to its revision)."""
    instance = EntityInstance(actor(definition.ref.entity_id, campaign), definition.ref, **instance_kwargs)
    return effective_entity(instance, index_of(definition))


def attempt(entity_id="iris", ability_id="light_bending", needs=("light", "concealment"), *,
            effect_id=None, conditions=("ambient_light",), attempt_id="t1-a", campaign=CAMPAIGN):
    return AbilityAttempt(attempt_id, actor(entity_id, campaign), ability_id, frozenset(needs),
                          "scene:s1/t1", effect_id, frozenset(conditions))


def flight(**kwargs):
    """Iris tries to fly by radiation pressure."""
    return attempt(needs=("light", "force", "flight"), **kwargs)


def ruling(the_attempt, decides="extrapolation", decision="approved", ruling_id="r1"):
    return AbilityRuling(ruling_id, the_attempt, decides, decision, "director")
