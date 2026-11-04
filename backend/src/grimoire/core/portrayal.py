"""Effective authored definitions: compose a pinned derived portrayal over its base (Sprint 022).

A reusable base (``catalog:elderfen-cast/character/mara@r1``) and a world portrayal derived from it
are separate, immutable revisions. A derived revision states its base **inside its own payload**
(``derivation.base``, pinned), and an accepted ``derived_variant`` link from its own source must
agree. Composition walks that single chain from the root down and returns a disposable
``EffectiveDefinition`` with field-level provenance. Nothing is copied, rewritten or matched by name.

Rules (one layer: composed parent P, child revision C):

- absent in C means **inherit**; a value in C **replaces** P's (``restated`` when equal) or is
  **local** when P has none; a name in C's ``derivation.clear_*`` is **cleared**, recorded, and
  never inferred from prose, ``null`` or empty text;
- ``alternate_portrayal``, ``same_individual``, ``overrides`` and equal names are never inheritance.

Two ``grimoire.entity/0`` payload keys, which 009 keeps verbatim in ``extras``, are interpreted here:
``sections`` (``{name: text}``) and ``derivation``. Other unknown extras are listed as
``uncomposed``, never composed.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from .entity import DERIVATION_KEY, EntityDefinition
from .facets import Facet, FacetError, FacetSet
from .identity import SourceRef, validate_segment
from .index import DefinitionIndex, DefinitionLink

Origin = Literal["local", "inherited", "replaced", "restated", "cleared"]
SECTIONS_KEY = "sections"
REGISTERED_KEYS = frozenset({SECTIONS_KEY, DERIVATION_KEY})
MAX_SECTIONS = 64
MAX_SECTION_TEXT = 8000
MAX_CHAIN = 8
_DERIVATION_FIELDS = frozenset({"base", "clear_sections", "clear_facets", "clear_description"})
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp"})  # control, surrogate, line/paragraph separators
_ALLOWED_CONTROLS = frozenset("\n\t")

#: ``read(pinned_ref)`` -> the decoded definition and the etag of the exact bytes (or ``None``).
Reader = Callable[[SourceRef], "tuple[EntityDefinition, str | None]"]


class PortrayalError(ValueError):
    """A definition, derivation or selection this slice refuses; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str, ref: SourceRef | None = None,
                 detail: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.ref = ref
        self.detail = detail

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason, self.ref, self.detail))


@dataclass(frozen=True, slots=True)
class ChainNode:
    """One pinned revision of a derivation chain and the etag of the bytes composed (if stored)."""

    ref: SourceRef
    etag: str | None = None


@dataclass(frozen=True, slots=True)
class FieldOrigin:
    """Where one effective field came from: ``name``, ``description``, ``section:<n>`` or ``facet:<n>``."""

    field: str
    origin: Origin
    source: ChainNode


@dataclass(frozen=True, slots=True)
class Notice:
    """Something a consumer should know that did not stop composition (``base_not_head``, ...)."""

    code: str
    ref: SourceRef
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Derivation:
    """A derived revision's own statement of its pinned base and what it clears."""

    base: SourceRef
    clear_sections: frozenset[str] = frozenset()
    clear_facets: frozenset[str] = frozenset()
    clear_description: bool = False


@dataclass(frozen=True, slots=True)
class EffectiveDefinition:
    """A disposable composed view of one pinned revision; never a canonical record.

    It holds no campaign memory, relationships, observations or control, and it never contains a
    cleared value. ``chain`` is leaf first.
    """

    requested: SourceRef
    chain: tuple[ChainNode, ...]
    name: str
    nature: str
    description: str
    traits: FacetSet
    sections: tuple[tuple[str, str], ...]
    provenance: tuple[FieldOrigin, ...]
    uncomposed: tuple[tuple[SourceRef, str], ...] = ()
    notices: tuple[Notice, ...] = ()
    ignored_links: tuple[DefinitionLink, ...] = ()

    @property
    def ref(self) -> SourceRef:
        return self.chain[0].ref

    def section(self, name: str) -> str | None:
        return dict(self.sections).get(name)

    def origin(self, field: str) -> FieldOrigin | None:
        return next((entry for entry in self.provenance if entry.field == field), None)


def _fail(message: str, reason: str, ref: SourceRef | None, detail: str | None = None) -> PortrayalError:
    return PortrayalError(message, reason, ref, detail)


def _unsafe(text: str) -> bool:
    return any(char not in _ALLOWED_CONTROLS and unicodedata.category(char) in _UNSAFE_CATEGORIES for char in text)


def _line_endings(text: str) -> str:
    """CRLF and lone CR (Windows editors, pasted card text) read as ``\n``; the stored text is unchanged."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def authored_description(definition: EntityDefinition) -> str:
    """The description exactly as 009 holds it, whitespace included.

    009 owns this field and accepts any text, so composition neither refuses nor rewrites it: a
    standalone card that 009 accepts stays usable, and the index path and a snapshot agree. Blank
    text only counts as *absence* when a child decides whether it overrides (it then inherits).
    """
    return definition.description


def authored_sections(definition: EntityDefinition) -> tuple[tuple[str, str], ...]:
    """The definition's authored sections, validated and sorted by name."""
    ref = definition.ref
    if SECTIONS_KEY not in definition.extras:
        return ()
    raw = definition.extras[SECTIONS_KEY]
    if not isinstance(raw, Mapping):
        raise _fail(f"{ref}: sections must be an object of named texts", "invalid_sections", ref)
    if len(raw) > MAX_SECTIONS:
        raise _fail(f"{ref}: more than {MAX_SECTIONS} sections", "invalid_sections", ref)
    sections = []
    for name, text in raw.items():
        if type(text) is str:
            text = _line_endings(text)
        try:
            validate_segment(name, "section name")
        except ValueError as error:
            raise _fail(f"{ref}: {error}", "invalid_sections", ref) from None
        if (type(text) is not str or not text.strip() or len(text) > MAX_SECTION_TEXT
                or _unsafe(text)):
            raise _fail(f"{ref}: section {name!r} must be nonblank text of at most {MAX_SECTION_TEXT} "
                        "characters without control characters", "invalid_sections", ref)
        sections.append((name, text))
    return tuple(sorted(sections))


def _names(value: object, label: str, ref: SourceRef) -> frozenset[str]:
    if not isinstance(value, (list, tuple)):
        raise _fail(f"{ref}: derivation.{label} must be a list of names", "invalid_derivation", ref)
    for name in value:
        try:
            validate_segment(name, f"derivation.{label}")
        except ValueError as error:
            raise _fail(f"{ref}: {error}", "invalid_derivation", ref) from None
    if len(set(value)) != len(value):
        raise _fail(f"{ref}: derivation.{label} names a value twice", "invalid_derivation", ref)
    return frozenset(value)


def derivation_of(definition: EntityDefinition) -> Derivation | None:
    """The revision's own derivation statement, or ``None`` for a complete (standalone) definition."""
    ref = definition.ref
    if DERIVATION_KEY not in definition.extras:
        return None
    raw = definition.extras[DERIVATION_KEY]  # present means derived: even null is refused, never ignored
    if not isinstance(raw, Mapping):
        raise _fail(f"{ref}: derivation must be an object", "invalid_derivation", ref)
    unknown = sorted(set(raw) - _DERIVATION_FIELDS)
    if unknown:
        raise _fail(f"{ref}: derivation has unknown fields {unknown}", "invalid_derivation", ref)
    try:
        base = SourceRef.parse(raw.get("base"))
    except ValueError as error:
        raise _fail(f"{ref}: derivation.base: {error}", "invalid_derivation", ref) from None
    if not base.pinned:
        raise _fail(f"{ref}: derivation.base must be a pinned revision: {base}", "invalid_derivation", ref)
    clear_description = raw.get("clear_description", False)
    if type(clear_description) is not bool:
        raise _fail(f"{ref}: derivation.clear_description must be true or false", "invalid_derivation", ref)
    return Derivation(base, _names(raw.get("clear_sections", ()), "clear_sections", ref),
                      _names(raw.get("clear_facets", ()), "clear_facets", ref), clear_description)


# -- the chain ----------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _Layer:
    definition: EntityDefinition
    node: ChainNode
    derivation: Derivation | None


#: ``explain(base)`` -> a store-specific reason a base does not resolve (``excluded_source``, ...), or ``None``.
Explain = Callable[[SourceRef], "str | None"]


def _walk(leaf: SourceRef, index: DefinitionIndex, read: Reader, explain: Explain | None = None
          ) -> tuple[list[_Layer], list[Notice], list[DefinitionLink]]:
    """Ancestry only: links, bases and derivation statements. No field (section, description) is read."""
    layers: list[_Layer] = []
    notices: list[Notice] = []
    ignored: list[DefinitionLink] = []
    current = leaf
    while True:
        if len(layers) == MAX_CHAIN:
            raise _fail(f"{leaf}: derivation chain is longer than {MAX_CHAIN}", "chain_too_deep", leaf)
        definition, etag = read(current)
        derivation = derivation_of(definition)
        layer = _Layer(definition, ChainNode(current, etag), derivation)
        layers.append(layer)
        stated = [link for link in index.links_for(current)
                  if link.relation == "derived_variant" and link.subject == current]
        own = [link for link in stated if link.declared_by == current.source]
        ignored += [link for link in stated if link.declared_by != current.source]
        if derivation is None:
            if own:
                notices.append(Notice("ancestry_only", current, ", ".join(sorted(str(l.object) for l in own))))
            return layers, notices, ignored
        base = derivation.base
        if not own:
            raise _fail(f"{current} derives from {base} but its source declares no such derived_variant link",
                        "unlinked_derivation", current)
        if any(link.object != base for link in own):
            raise _fail(f"{current}: derived_variant links disagree with its derivation base {base}",
                        "link_mismatch", current)
        if base.kind != current.kind:
            raise _fail(f"{current} is a {current.kind}; its base {base} is a {base.kind}", "kind_mismatch", current)
        broken = index.check(base)
        if broken is not None:
            why = (explain(base) if explain is not None else None) or broken.reason
            raise _fail(f"{current}: base {base} does not resolve ({why})", "missing_base", current, why)
        head = index.resolve(base.unpinned()).ref
        if head != base:
            notices.append(Notice("base_not_head", base, head.revision or ""))
        current = base


# -- composition --------------------------------------------------------------------------------

class _State:
    def __init__(self, root: _Layer, standalone: bool = False) -> None:
        node = root.node
        self.name = root.definition.name
        self.nature = root.definition.nature
        self.origins: dict[str, FieldOrigin] = {"name": FieldOrigin("name", "local", node)}
        self.description = authored_description(root.definition)
        if self.description.strip():
            self.origins["description"] = FieldOrigin("description", "local", node)
        self.unusable_sections: PortrayalError | None = None
        try:
            self.sections: dict[str, str] = dict(authored_sections(root.definition))
        except PortrayalError as error:
            if not standalone:
                raise  # a derived chain never silently drops a layer's authored content
            self.sections, self.unusable_sections = {}, error
        self.facets: dict[str, Facet] = {facet.name: facet for facet in root.definition.traits}
        for name in self.sections:
            self.origins[f"section:{name}"] = FieldOrigin(f"section:{name}", "local", node)
        for name in self.facets:
            self.origins[f"facet:{name}"] = FieldOrigin(f"facet:{name}", "local", node)

    def inherit_all(self) -> None:
        """Values the child does not mention keep their author; only ``cleared`` stays as it is."""
        for key, entry in self.origins.items():
            if entry.origin != "cleared":
                self.origins[key] = FieldOrigin(key, "inherited", entry.source)

    def set(self, key: str, present: bool, equal: bool, node: ChainNode) -> None:
        origin: Origin = "local" if not present else ("restated" if equal else "replaced")
        self.origins[key] = FieldOrigin(key, origin, node)

    def clear(self, key: str, present: bool, setting: bool, node: ChainNode) -> None:
        ref = node.ref
        if setting:
            raise _fail(f"{ref} both sets and clears {key}", "clear_and_set", ref)
        if not present:
            raise _fail(f"{ref} clears {key}, which its base does not have", "stale_clear", ref)
        self.origins[key] = FieldOrigin(key, "cleared", node)


def _apply(state: _State, layer: _Layer) -> None:
    child, node, derivation = layer.definition, layer.node, layer.derivation
    ref = node.ref
    assert derivation is not None  # every layer above the root is derived
    if child.nature != state.nature:
        raise _fail(f"{ref} is {child.nature!r} but its base is {state.nature!r}", "nature_mismatch", ref)
    state.inherit_all()
    state.set("name", True, child.name == state.name, node)
    state.name = child.name

    description = authored_description(child)  # blank text is absence (inherit), never an implicit clear
    present = bool(state.description.strip())
    if derivation.clear_description:
        state.clear("description", present, bool(description.strip()), node)
        state.description = ""
    elif description.strip():
        state.set("description", present, description == state.description, node)
        state.description = description

    sections = dict(authored_sections(child))
    for name in sorted(derivation.clear_sections):
        state.clear(f"section:{name}", name in state.sections, name in sections, node)
        del state.sections[name]
    for name, text in sections.items():
        state.set(f"section:{name}", name in state.sections, state.sections.get(name) == text, node)
        state.sections[name] = text

    facets = {facet.name: facet for facet in child.traits}
    for name in sorted(derivation.clear_facets):
        state.clear(f"facet:{name}", name in state.facets, name in facets, node)
        del state.facets[name]
    for name, facet in facets.items():
        state.set(f"facet:{name}", name in state.facets, state.facets.get(name) == facet, node)
        state.facets[name] = facet
    try:
        FacetSet(tuple(state.facets.values()))
    except FacetError as error:
        raise _fail(f"{ref}: {error}", error.reason, ref) from None


def derivation_chain(ref: SourceRef, index: DefinitionIndex, read: Reader,
                     explain: Explain | None = None) -> tuple[ChainNode, ...]:
    """The pinned chain of ``ref`` (leaf first), checking links and bases but reading no field.

    It succeeds whenever the ancestry is sound, even if a field rule (``invalid_sections``,
    ``stale_clear``, ...) would make ``compose`` fail, so merge owners can still reach a portrayal's
    pinned base to repair it.
    """
    layers, _, _ = _walk(index.resolve(ref).ref, index, read, explain)
    return tuple(layer.node for layer in layers)


def compose(ref: SourceRef, index: DefinitionIndex, read: Reader,
            explain: Explain | None = None) -> EffectiveDefinition:
    """The effective definition of ``ref`` (a pinned revision, or the head of an unpinned ref).

    The leaf must resolve (``MissingDefinition`` otherwise). ``read`` decodes one pinned revision
    and raises ``PortrayalError`` (``not_entity_definition``, ``undecodable``) when it cannot.
    """
    if not isinstance(ref, SourceRef):
        raise TypeError(f"expected a SourceRef, not {type(ref).__name__}")
    leaf = index.resolve(ref).ref
    layers, notices, ignored = _walk(leaf, index, read, explain)
    state = _State(layers[-1], standalone=len(layers) == 1)
    for layer in reversed(layers[:-1]):
        _apply(state, layer)
    skipped = {SECTIONS_KEY} if state.unusable_sections is not None else set()
    uncomposed = tuple(sorted(((layer.node.ref, key) for layer in layers
                               for key in layer.definition.extras if key not in REGISTERED_KEYS or key in skipped),
                              key=lambda item: (str(item[0]), item[1])))
    if state.unusable_sections is not None:  # a standalone card stays usable, as 009 accepts it
        notices.append(Notice("invalid_sections", leaf, str(state.unusable_sections)))
    return EffectiveDefinition(
        requested=ref,
        chain=tuple(layer.node for layer in layers),
        name=state.name,
        nature=state.nature,
        description=state.description,
        traits=FacetSet(tuple(state.facets.values())),
        sections=tuple(sorted(state.sections.items())),
        provenance=tuple(state.origins[key] for key in sorted(state.origins)),
        uncomposed=uncomposed,
        notices=tuple(notices),
        ignored_links=tuple(ignored),
    )


def read_entities(index: DefinitionIndex) -> Reader:
    """A reader for an index that already holds ``EntityDefinition``s (no stored etags)."""
    def read(ref: SourceRef) -> tuple[EntityDefinition, str | None]:
        definition = index.resolve(ref)
        if not isinstance(definition, EntityDefinition):
            raise _fail(f"{ref} is a {type(definition).__name__}, not an entity definition",
                        "not_entity_definition", ref)
        return definition, None
    return read


__all__ = [
    "MAX_CHAIN", "MAX_SECTIONS", "MAX_SECTION_TEXT", "REGISTERED_KEYS", "ChainNode", "Derivation",
    "EffectiveDefinition", "Explain", "FieldOrigin", "Notice", "PortrayalError", "Reader", "authored_description",
    "authored_sections",
    "compose", "derivation_chain", "derivation_of", "read_entities",
]
