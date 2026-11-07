"""Inherited defaults and local overrides: inspect and edit a 022 derived portrayal field by field (046).

022 composes a derived portrayal over its **pinned** base and records each field's provenance. This
module answers the inspector's question about every field, and how it compares with the base's
current head (the *source diff*), and edits a derived payload one field at a time so a new revision
can override, clear or reset exactly that field and nothing else.

Field states (``FieldView.state``):

- ``inherited``: the value comes from the base chain, and the pinned base is its entity's head;
- ``pinned``: the value comes from the base chain, but the pinned base is not the head, so a later
  base revision does not reach it until a reviewed rebase (042) re-pins it;
- ``overridden``: this revision replaces an inherited value (022 ``replaced``);
- ``restated``: this revision repeats the inherited value, so it no longer follows the base;
- ``local``: this revision authors a value the base never had;
- ``cleared``: this revision explicitly clears an inherited value (a typed clear, never ``null``,
  blank text or prose). An ancestor's clear is ``inherited`` (or ``pinned``), with ``source`` the
  revision that cleared it;
- ``missing``: the base's head has a value this revision neither shows nor cleared: the pinned base
  lacked it, so it arrives only with a rebase.

``FieldView.upstream`` compares the pinned base with the base's head for that field: ``same``,
``changed``, ``removed`` (the head no longer has it: a deprecated source value, shown, never
dropped silently) or ``added`` (only the head has it). It is ``""`` for a standalone definition, and
for a portrayal whose base is its head (nothing to compare). Names are never matched: fields are keyed
by their 022 provenance field names (``name``, ``description``, ``section:<n>``, ``facet:<n>``).

Pure: no files, clock or model. ``grimoire.effective.inspect_definition`` composes the three sides.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .entity import DERIVATION_KEY
from .identity import SourceRef, validate_segment
from .json_value import JsonObject, thaw
from .portrayal import SECTIONS_KEY, EffectiveDefinition, Notice

FIELD_STATES = ("inherited", "pinned", "overridden", "restated", "local", "cleared", "missing")
UPSTREAM = ("", "same", "changed", "removed", "added")
_STATE_OF = {"inherited": "inherited", "replaced": "overridden", "restated": "restated", "local": "local",
             "cleared": "cleared"}


@dataclass(frozen=True)
class FieldView:
    """One field of a portrayal as an inspector shows it.

    ``source`` is the pinned revision that authored the value shown (or cleared it). For ``missing``
    it is the base head that has the value.
    """

    field: str
    state: str
    source: SourceRef
    upstream: str = ""


@dataclass(frozen=True)
class Inspection:
    ref: SourceRef
    #: the pinned base revision (``None`` for a standalone definition)
    base: SourceRef | None
    #: the base entity's head, when it is a different revision than ``base``
    base_head: SourceRef | None
    fields: tuple[FieldView, ...]
    notices: tuple[Notice, ...] = ()

    def field(self, name: str) -> FieldView | None:
        return next((view for view in self.fields if view.field == name), None)


def _values(definition: EffectiveDefinition) -> dict[str, object]:
    values: dict[str, object] = {"name": definition.name}
    if definition.description.strip():
        values["description"] = definition.description
    values.update({f"section:{name}": text for name, text in definition.sections})
    values.update({f"facet:{facet.name}": facet for facet in definition.traits})
    return values


def _upstream(field: str, pinned: Mapping[str, object], head: Mapping[str, object] | None) -> str:
    if head is None:
        return ""
    if field in pinned and field in head:
        return "same" if pinned[field] == head[field] else "changed"
    if field in pinned:
        return "removed"
    return "added" if field in head else ""


def inspect(leaf: EffectiveDefinition, base: EffectiveDefinition | None = None,
            base_head: EffectiveDefinition | None = None) -> Inspection:
    """Every field of ``leaf`` with its state and source diff.

    ``base`` is the composed **pinned** base (``leaf.chain[1]``) and ``base_head`` the composed head of
    that base's entity; both ``None`` for a standalone definition. They must be the leaf's own base:
    anything else is a ``ValueError``, never a comparison with another entity.
    """
    if (base is None) != (len(leaf.chain) == 1):
        raise ValueError("a derived portrayal needs its composed pinned base; a standalone one has none")
    if base is not None and base.ref != leaf.chain[1].ref:
        raise ValueError(f"{base.ref} is not {leaf.ref}'s pinned base {leaf.chain[1].ref}")
    if base_head is not None and (base is None or base_head.ref.entity_key != base.ref.entity_key):
        raise ValueError("base_head must be a revision of the pinned base's own entity")
    behind = base is not None and base_head is not None and base_head.ref != base.ref
    pinned_values = _values(base) if base is not None else {}
    head_values = _values(base_head) if behind else None  # type: ignore[arg-type]
    views: list[FieldView] = []
    for origin in leaf.provenance:
        state = _STATE_OF[origin.origin]
        if state == "cleared" and origin.source.ref != leaf.ref:
            state = "inherited"  # 022 keeps an ancestor's clear as ``cleared``: here it is inherited
        if state == "inherited" and behind:
            state = "pinned"
        upstream = _upstream(origin.field, pinned_values, head_values)
        if state == "local" and upstream == "":
            upstream = "added" if head_values is not None and origin.field in head_values else ""
        views.append(FieldView(origin.field, state, origin.source.ref, upstream))
    if head_values is not None:
        shown = {view.field for view in views}
        for field in sorted(set(head_values) - shown):
            views.append(FieldView(field, "missing", base_head.ref, "added"))  # type: ignore[union-attr]
    return Inspection(leaf.ref, base.ref if base is not None else None,
                      base_head.ref if behind else None,  # type: ignore[union-attr]
                      tuple(sorted(views, key=lambda view: view.field)), leaf.notices)


# -- editing a derived payload, one field at a time ------------------------------------------------

class OverrideError(ValueError):
    """An edit refused; ``reason`` is machine-readable (``not_derived``, ``invalid_field``)."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def _plain(value: object):  # type: ignore[no-untyped-def]
    """A plain, independent copy of a stored payload (read-only mappings and tuples included)."""
    if isinstance(value, JsonObject):
        value = thaw(value)
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _derived(payload: Mapping[str, object]) -> tuple[dict, dict]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get(DERIVATION_KEY), Mapping):
        raise OverrideError("only a derived portrayal inherits: this payload has no derivation object",
                            "not_derived")
    edited = _plain(payload)
    derivation = dict(edited[DERIVATION_KEY])
    edited[DERIVATION_KEY] = derivation
    return edited, derivation


def _segment(name: object, label: str) -> str:
    try:
        validate_segment(name, label)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise OverrideError(f"{label}: {error}", "invalid_field") from None
    return name  # type: ignore[return-value]


def _drop(derivation: dict, key: str, name: str) -> None:
    names = [value for value in derivation.get(key, ()) if value != name]
    if names:
        derivation[key] = names
    else:
        derivation.pop(key, None)


def override_section(payload: Mapping[str, object], name: str, text: str) -> dict:
    """A copy of a derived ``payload`` whose next revision replaces section ``name`` with ``text``
    (and no longer clears it). Text rules are 022's, checked when the revision composes."""
    edited, derivation = _derived(payload)
    _segment(name, "section")
    if type(text) is not str or not text.strip():
        raise OverrideError("an override needs nonblank text; to remove a section, clear it", "invalid_field")
    sections = dict(edited.get(SECTIONS_KEY) or {})
    sections[name] = text
    edited[SECTIONS_KEY] = sections
    _drop(derivation, "clear_sections", name)
    return edited


def clear_section(payload: Mapping[str, object], name: str) -> dict:
    """A copy whose next revision clears inherited section ``name`` (022 refuses a stale clear)."""
    edited, derivation = _derived(payload)
    _segment(name, "section")
    _forget_section(edited, name)
    derivation["clear_sections"] = sorted(set(derivation.get("clear_sections", ())) | {name})
    return edited


def reset_section(payload: Mapping[str, object], name: str) -> dict:
    """A copy whose next revision inherits section ``name`` again: its own value and clear are removed."""
    edited, derivation = _derived(payload)
    _segment(name, "section")
    _forget_section(edited, name)
    _drop(derivation, "clear_sections", name)
    return edited


def _forget_section(edited: dict, name: str) -> None:
    sections = dict(edited.get(SECTIONS_KEY) or {})
    sections.pop(name, None)
    if sections:
        edited[SECTIONS_KEY] = sections
    else:
        edited.pop(SECTIONS_KEY, None)


def override_description(payload: Mapping[str, object], text: str) -> dict:
    edited, derivation = _derived(payload)
    if type(text) is not str or not text.strip():
        raise OverrideError("blank text inherits the description; to remove it, clear it", "invalid_field")
    edited["description"] = text
    derivation.pop("clear_description", None)
    return edited


def clear_description(payload: Mapping[str, object]) -> dict:
    edited, derivation = _derived(payload)
    edited.pop("description", None)
    derivation["clear_description"] = True
    return edited


def reset_description(payload: Mapping[str, object]) -> dict:
    edited, derivation = _derived(payload)
    edited.pop("description", None)
    derivation.pop("clear_description", None)
    return edited


def clear_facet(payload: Mapping[str, object], name: str) -> dict:
    """A copy whose next revision clears inherited facet ``name`` (and stops restating it)."""
    edited, derivation = _derived(payload)
    _segment(name, "facet")
    _forget_facet(edited, name)
    derivation["clear_facets"] = sorted(set(derivation.get("clear_facets", ())) | {name})
    return edited


def reset_facet(payload: Mapping[str, object], name: str) -> dict:
    """A copy whose next revision inherits facet ``name`` again. 022's first-slice limit still holds:
    a facet the revision keeps that requires this one must then be restated by its author."""
    edited, derivation = _derived(payload)
    _segment(name, "facet")
    _forget_facet(edited, name)
    _drop(derivation, "clear_facets", name)
    return edited


def _forget_facet(edited: dict, name: str) -> None:
    traits = edited.get("traits")
    if isinstance(traits, list):
        edited["traits"] = [facet for facet in traits
                            if not (isinstance(facet, Mapping) and facet.get("facet") == name)]


__all__ = [
    "FIELD_STATES", "UPSTREAM", "FieldView", "Inspection", "OverrideError", "clear_description", "clear_facet",
    "clear_section", "inspect", "override_description", "override_section", "reset_description", "reset_facet",
    "reset_section",
]
