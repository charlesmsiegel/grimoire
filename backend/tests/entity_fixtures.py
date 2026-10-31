"""Synthetic definitions for the entity-facet scenarios (no user content).

A person, a generic species, a particular creature and a sentient sword, all
the same ``EntityDefinition`` type with different facets.
"""

from grimoire.core import DefinitionIndex, SourceDescriptor, SourceRef
from grimoire.core.entity import EntityDefinition
from grimoire.core.facets import (
    Agent, Biological, Communicative, Durable, FacetSet, Physical, Portable,
)

EMBER = "world:emberfall"
LANTERN = "catalog:lanternworks"
VOICE = Communicative(speaks={"speech"}, understands={"speech"})


def ref(kind, entity_id, revision="r1", source=EMBER):
    return SourceRef(source, kind, entity_id, revision)


def mara(revision="r1"):
    return EntityDefinition(ref("character", "mara", revision), "Mara", "individual",
                            FacetSet([Physical(), Biological(), Agent(), VOICE]),
                            "An observant negotiator")


def ogre():
    return EntityDefinition(ref("species", "ogre"), "Ogre", "template",
                            FacetSet([Physical(size="large"), Durable(30), Biological(), Agent()]))


def grakka():
    return EntityDefinition(ref("creature", "grakka"), "Grakka the Ogre Chieftain", "individual",
                            FacetSet([Physical(size="huge"), Durable(60), Biological(), Agent()]))


def vessa(revision="r1", *, agent=True, max_integrity=12, source=EMBER):
    traits = [Physical(material="steel", size="small"), Portable("light"), Durable(max_integrity)]
    if agent:
        traits += [Agent(), VOICE]
    return EntityDefinition(ref("item", "vessa", revision, source), "Vessa", "individual",
                            FacetSet(traits), "A sentient sword")


def plain_sword(revision="r1"):
    return EntityDefinition(ref("item", "longsword", revision), "Longsword", "individual",
                            FacetSet([Physical(material="steel"), Portable(), Durable(10)]))


def index_of(*definitions, heads=None, links=()):
    """Register exactly the sources the given definitions come from."""
    sources = sorted({definition.ref.source for definition in definitions})
    return DefinitionIndex([SourceDescriptor(source, owner="user") for source in sources],
                           definitions, heads, links)
