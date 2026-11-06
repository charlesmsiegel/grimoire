"""Art lineage through 022 derived portrayals (Sprint 046): which entities' placements a subject layers.

A placement attaches to an **entity**, across its revisions (018), so art inherits **live**: an
entity's lineage is read from its *head* revision's own ``derivation`` (022), and every ancestor's
current placements are layered beneath it. A head with no ``derivation`` key is standalone, exactly
as 018 resolved it, and costs no library load. A derived head walks 022's own ``derivation_chain``
over one ``LibraryView``, so art and fields agree about ancestry: a missing, excluded, unlinked or
mismatched base inherits **nothing**, with the reason as a problem, and never a namesake's picture.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from grimoire.core.entity import DERIVATION_KEY
from grimoire.core.identity import SourceRef
from grimoire.core.index import MissingDefinition
from grimoire.core.portrayal import PortrayalError, derivation_chain
from grimoire.effective import entity_decoder, explainer
from grimoire.images import Problem
from grimoire.store import LibraryView, RecordEnvelope, StoredDefinition


def is_derived(envelope: RecordEnvelope) -> bool:
    """Whether a stored definition revision is a 022 derived delta: its payload has a ``derivation`` key
    (even ``null``, which 022 refuses rather than ignores, so it inherits nothing either)."""
    payload = envelope.payload
    return isinstance(payload, Mapping) and DERIVATION_KEY in payload


@dataclass(frozen=True)
class Lineage:
    """An entity's art lineage: ``entities`` root first, ending in the entity itself; ``derived[i]``
    says whether ``entities[i]``'s placements may ``hide``, ``adjust`` or ``inherit``."""

    entities: tuple[SourceRef, ...]
    derived: tuple[bool, ...]
    problems: tuple[Problem, ...] = ()


def lineage(entity: SourceRef, definition: Callable[[SourceRef], StoredDefinition | str],
            view: Callable[[], LibraryView]) -> Lineage:
    """The lineage of an unpinned ``entity`` whose head ``definition`` reads (the caller has checked it
    resolves). ``view`` loads the library once, and only for a derived head."""
    found = definition(entity)
    if not isinstance(found, StoredDefinition) or not is_derived(found.envelope):
        return Lineage((entity,), (False,))
    library = view()

    def read(ref: SourceRef):  # type: ignore[no-untyped-def]
        stored = library.index.resolve(ref)
        return entity_decoder(stored), getattr(stored, "etag", None)

    try:
        chain = derivation_chain(found.ref, library.index, read, explainer(library))
    except PortrayalError as error:
        why = f"{error.reason}/{error.detail}" if error.detail else error.reason
        return Lineage((entity,), (True,), (Problem(str(entity), error.reason,
                                                    f"{why}: its base's art is not inherited"),))
    except MissingDefinition as error:  # the load and the head read disagree: inherit nothing
        return Lineage((entity,), (True,), (Problem(str(entity), error.reason,
                                                    "its base's art is not inherited"),))
    entities = tuple(node.ref.unpinned() for node in reversed(chain))
    return Lineage(entities, tuple(index > 0 for index in range(len(entities))))


__all__ = ["Lineage", "is_derived", "lineage"]
