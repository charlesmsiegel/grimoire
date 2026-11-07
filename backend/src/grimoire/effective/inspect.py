"""The field inspector over one loaded library (Sprint 046): compose a portrayal, its pinned base and
that base's head from **one** ``LibraryView``, then ``grimoire.core.inheritance.inspect`` them.

Nothing here writes, reads files itself, or calls a model. Composition refusals of the requested
revision or its pinned base propagate (``PortrayalError``, ``MissingDefinition``): an inspector never
shows a portrayal its chain cannot compose. A base head that cannot compose leaves the source diff
out, with a ``base_head_unavailable`` notice, because the portrayal itself is still sound.
"""

from __future__ import annotations

from grimoire.core.identity import SourceRef
from grimoire.core.index import MissingDefinition
from grimoire.core.inheritance import Inspection, inspect
from grimoire.core.portrayal import EffectiveDefinition, Notice, PortrayalError, Reader, compose
from grimoire.store import LibraryView

from .library import entity_decoder, explainer


def view_reader(view: LibraryView) -> Reader:
    """A 022 reader over one view: each pinned revision decoded, with the etag of its stored bytes."""
    def read(ref: SourceRef):  # type: ignore[no-untyped-def]
        stored = view.index.resolve(ref)
        return entity_decoder(stored), getattr(stored, "etag", None)
    return read


def compose_in(view: LibraryView, ref: SourceRef) -> EffectiveDefinition:
    return compose(ref, view.index, view_reader(view), explainer(view))


def inspect_definition(view: LibraryView, ref: SourceRef) -> Inspection:
    """The inspector's view of ``ref`` (a pinned revision, or an entity's head)."""
    leaf = compose_in(view, ref)
    if len(leaf.chain) == 1:
        return inspect(leaf)
    base = compose_in(view, leaf.chain[1].ref)
    try:
        head = compose_in(view, base.ref.unpinned())
    except (PortrayalError, MissingDefinition) as error:
        reason = getattr(error, "reason", "missing")
        inspection = inspect(leaf, base)
        return Inspection(inspection.ref, inspection.base, None, inspection.fields,
                          inspection.notices + (Notice("base_head_unavailable", base.ref, str(reason)),))
    return inspect(leaf, base, head)


__all__ = ["compose_in", "inspect_definition", "view_reader"]
