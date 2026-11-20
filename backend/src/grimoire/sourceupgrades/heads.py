"""Every entity of one loaded source at its head, as the index resolves it (079)."""
from __future__ import annotations

from grimoire.core.identity import SourceRef
from grimoire.core.index import MissingDefinition
from grimoire.store import LibraryView, StoredDefinition


def entity_heads(view: LibraryView, source: str) -> dict[SourceRef, StoredDefinition]:
    """Unpinned ref to head record for each entity the index holds for ``source``.

    A manifest need not declare every head; the index then chooses one, so the declared heads
    alone would miss entities a reference can still reach.
    """
    entities = {definition.ref.unpinned() for definition in view.index.definitions()
                if definition.ref.source == source}
    heads = {}
    for ref in sorted(entities, key=str):
        try:
            found = view.index.resolve(ref)
        except MissingDefinition:
            continue
        if isinstance(found, StoredDefinition):
            heads[ref] = found
    return heads
