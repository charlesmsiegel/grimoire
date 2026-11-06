"""The definition a reviewed chat actor becomes in a new reusable world (042).

A chat's actor is a *lived* individual: its source card or portrayal, the campaign's overrides, and
its history. Promoting it to a world copies only the **authored adaptation**, never the lived part:

- a sourced actor becomes a **derived portrayal** over the exact pinned revision its chat binding
  resolved to. It states the actor's effective name and only the facets the campaign overrode (with
  the facets those need, restated, so the portrayal's own traits are a valid set, 022's first-slice
  limit). Everything else, the description and sections included, is inherited from the base, so a
  later reviewed rebase of the portrayal can still follow it;
- a campaign original (no source) becomes a standalone individual with its effective name and facets.

Lived state (integrity), relations, events, knowledge and transcript are never copied. Each
definition records where it came from under ``promoted_from`` (an uncomposed extra): the chat, the
actor and the etag of the actor file the proposal was made from.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.entity import DERIVATION_KEY, EffectiveEntity, EntityDefinition, EntityInstance
from grimoire.core.facets import Facet, FacetSet, UnknownFacet
from grimoire.core.identity import SourceRef

PROMOTED_FROM = "promoted_from"


def _with_requirements(chosen: Mapping[str, Facet], composed: FacetSet) -> FacetSet:
    """``chosen`` plus, from ``composed``, every known facet a chosen one requires (transitively)."""
    picked = dict(chosen)
    available = {facet.name: facet for facet in composed}
    while True:
        missing = {name for facet in picked.values() if not isinstance(facet, UnknownFacet)
                   for name in facet.requires} - set(picked)
        missing &= set(available)
        if not missing:
            return FacetSet(tuple(picked.values()))
        for name in missing:
            picked[name] = available[name]


def canon_definition(ref: SourceRef, instance: EntityInstance, entity: EffectiveEntity,
                     base: SourceRef | None, nature: str, provenance: Mapping[str, str]) -> EntityDefinition:
    """The new world's definition ``ref`` for one actor.

    ``entity`` is the actor composed over its selection (its effective name and facets); ``base`` is
    the pinned revision its binding resolved to (``None`` for a campaign original) and ``nature`` that
    base's composed nature (a portrayal keeps its base's nature, 022's ``nature_mismatch``).
    """
    if not isinstance(ref, SourceRef) or not ref.pinned:
        raise ValueError(f"a promoted definition is one pinned revision, not {ref}")
    extras: dict[str, object] = {PROMOTED_FROM: dict(provenance)}
    if base is None:
        return EntityDefinition(ref, entity.name, "individual", entity.facets, entity.description, extras)
    if not base.pinned:
        raise ValueError(f"a portrayal's base is a pinned revision, not {base}")
    overridden = {facet.name: facet for facet in (*instance.replaced, *instance.added)}
    traits = _with_requirements({name: entity.facets.by_name(name) or facet  # composed value wins
                                 for name, facet in overridden.items()}, entity.facets)
    extras[DERIVATION_KEY] = {"base": str(base)}
    return EntityDefinition(ref, entity.name, nature, traits, "", extras)
