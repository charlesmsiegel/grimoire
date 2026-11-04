"""Synthetic Elderfen portrayals of one reusable Mara (Sprint 022). No user content.

A shared catalog base, a Modern portrayal that replaces period facts, a Vampire portrayal that
replaces species and clears mortal facts, and a disconnected namesake.
"""

from __future__ import annotations

from grimoire.core import DefinitionIndex, DefinitionLink, SourceDescriptor, SourceRef
from grimoire.core.entity import EntityDefinition
from grimoire.core.facets import Agent, Biological, Communicative, FacetSet, Physical

CAST, MODERN, VAMPIRE, ELSEWHERE = ("catalog:elderfen-cast", "world:elderfen-modern", "world:elderfen-vampire",
                                    "world:elsewhere")
BASE = SourceRef(CAST, "character", "mara", "r1")
MODERN_MARA = SourceRef(MODERN, "character", "mara", "r1")
VAMPIRE_MARA = SourceRef(VAMPIRE, "character", "mara", "r1")
NAMESAKE = SourceRef(ELSEWHERE, "character", "mara", "r1")
VOICE = Communicative(speaks={"speech"}, understands={"speech"})

BASE_SECTIONS = {
    "personality": "Wry, patient, keeps promises.",
    "voice-evidence": "Answers questions with questions.",
    "appearance": "Grey eyes, ink-stained fingers.",
    "age": "Thirty-four.",
    "occupation": "Archivist.",
    "background": "Raised in a river town.",
    "mortal-family": "A younger brother who writes letters.",
    "species": "Human.",
    "period-customs": "Seals her letters with wax.",
}


def base(sections=None, **extras) -> EntityDefinition:
    return EntityDefinition(BASE, "Mara", "individual", FacetSet([Physical(), Biological(), Agent(), VOICE]),
                            "A careful keeper of records.", {"sections": sections or BASE_SECTIONS, **extras})


def derived(ref: SourceRef, base_ref: SourceRef = BASE, *, name="Mara", nature="individual", traits=(),
            description="", sections=None, **derivation) -> EntityDefinition:
    extras = {"derivation": {"base": str(base_ref), **derivation}}
    if sections:
        extras["sections"] = sections
    return EntityDefinition(ref, name, nature, FacetSet(list(traits)), description, extras)


def modern() -> EntityDefinition:
    return derived(MODERN_MARA, sections={"age": "Twenty-nine.", "occupation": "Data librarian.",
                                          "background": "Grew up above a laundromat."},
                   clear_sections=["period-customs"])


def vampire() -> EntityDefinition:
    return derived(VAMPIRE_MARA, sections={"species": "Vampire, turned in 1891."},
                   clear_sections=["mortal-family"], clear_facets=["biological"])


def namesake() -> EntityDefinition:
    return EntityDefinition(NAMESAKE, "Mara", "individual", FacetSet([Agent()]), "Someone else entirely.")


def derives(subject: SourceRef, obj: SourceRef = BASE, declared_by: str | None = None) -> DefinitionLink:
    return DefinitionLink(subject, "derived_variant", obj, declared_by or subject.source)


def elderfen_index(*definitions, links=None, heads=None, extra_sources=()) -> DefinitionIndex:
    """The base, both portrayals and the namesake, each portrayal linked by its own source."""
    definitions = definitions or (base(), modern(), vampire(), namesake())
    if links is None:
        links = [derives(d.ref, SourceRef.parse(d.extras["derivation"]["base"]))
                 for d in definitions if "derivation" in getattr(d, "extras", {})]
    sources = sorted({d.ref.source for d in definitions} | set(extra_sources)
                     | {link.declared_by for link in links})
    return DefinitionIndex([SourceDescriptor(source, owner="human:owner") for source in sources],
                           definitions, heads, links)


# -- the same portrayals in a real native store ---------------------------------------------------

NOTES = SourceRef(CAST, "lorebook", "river-notes", "r1")


def envelope(definition: EntityDefinition):
    from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_to_payload
    from grimoire.store import RecordEnvelope
    return RecordEnvelope(definition.ref, definition_to_payload(definition), name=definition.name,
                          extra={"schema": ENTITY_SCHEMA})


def manifest(source: str, *links: DefinitionLink, heads=None):
    from grimoire.store import LinkSpec, SourceManifest
    return SourceManifest(source, "human:owner", source, heads or {},
                          tuple(LinkSpec(link.subject, link.relation, link.object) for link in links))


def seed_elderfen(store, handle) -> None:
    """Base, Modern and Vampire Mara (each portrayal linked by its own world), a namesake and a lorebook."""
    from grimoire.store import RecordEnvelope
    txn = store.transaction()
    txn.put(manifest(CAST), None)
    txn.put(envelope(base()), None)
    txn.put(RecordEnvelope(NOTES, {"entries": ["the ferry runs at dusk"]}, name="River notes"), None)
    txn.put(manifest(MODERN, derives(MODERN_MARA)), None)
    txn.put(envelope(modern()), None)
    txn.put(manifest(VAMPIRE, derives(VAMPIRE_MARA)), None)
    txn.put(envelope(vampire()), None)
    txn.put(manifest(ELSEWHERE), None)
    txn.put(envelope(namesake()), None)
    txn.commit(handle)
