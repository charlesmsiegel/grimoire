"""Three-way field review of one campaign instance against a newer revision of its source (042).

The **ancestor** is the exact revision the instance was last based on, the **upstream** is a newer
revision of the *same* entity, and the instance's own overrides (``local_name``, ``replaced`` and
``added`` facets) are the campaign's side. Both definitions are compared as 022 effective
definitions, so a derived upstream portrayal is compared by its composed values, never by its raw
delta. Nothing here is matched by name: a namesake, or any other entity, is never a rebase target.

Fields are named as 022's provenance names them: ``name``, ``description``, ``section:<n>`` and
``facet:<n>``, plus ``extra:<key>`` for a payload key 022 does not compose (``uncomposed``: 037's
``card``, with a card's example dialogue, greetings and system prompt, which 035 reads for the
actor's voice and opening). An ``extra:`` row compares the key's value in every layer of the chain,
leaf first, and lists the paths that changed in ``changed``. Only fields that changed upstream or
that the instance overrides get a row:

| status | meaning | decision |
|---|---|---|
| ``upstream`` | upstream changed it; the instance does not override it | required |
| ``conflict`` | upstream changed it, and the instance overrides it with another value | required |
| ``converged`` | upstream changed it to exactly the instance's override | none: the override stays |
| ``local`` | the instance overrides it; upstream did not change it | none: the override stays |
| ``individual`` | upstream renamed the template of an individualized (024) instance | none: its given name stays |

``take`` adopts the upstream value (and drops the instance's override). ``keep`` keeps the
campaign's value as an instance override: the ancestor value on an ``upstream`` row, the override
on a ``conflict`` row. Only what an instance can carry can be kept (a name as ``local_name``, a
facet as ``replaced`` or ``added``). Description and sections have no instance override in this
slice, an instance has no override of payload extras (a campaign keeping its own voice or greeting is 046's
design), and an instance cannot suppress a trait its source adds, so ``keep`` there is
``not_overridable``: the campaign stays pinned instead.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Literal, Union

from grimoire.core.entity import EffectiveEntity, EntityInstance, compose_instance
from grimoire.core.facets import Facet, FacetBundle
from grimoire.core.identity import SourceRef
from grimoire.core.json_value import JsonObject, JsonValue
from grimoire.core.portrayal import EffectiveDefinition

Status = Literal["upstream", "conflict", "converged", "local", "individual"]
Decision = Literal["take", "keep"]
DECISIONS = frozenset({"take", "keep"})
#: the field-name prefix of an uncomposed payload key's row
EXTRA_PREFIX = "extra:"
#: a field's value: text (name, description, a section), a facet, an uncomposed key's value in each
#: layer of the chain (leaf first; ``None`` where a layer lacks it), or ``None`` (absent)
Value = Union[str, Facet, tuple[JsonValue, ...], None]
#: one revision's payload extras (009 ``EntityDefinition.extras``), for ``extra:`` rows
ExtrasReader = Callable[[SourceRef], Mapping[str, object]]
#: nesting ``changed`` follows inside an extra; a deeper difference is reported at that depth
_MAX_PATH_DEPTH = 16


class RebaseError(ValueError):
    """A rebase this slice refuses; nothing was written. ``reason`` is machine-readable.

    ``fields`` names the fields the refusal is about (an undecided or not-overridable field).
    """

    def __init__(self, message: str, reason: str, fields: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.reason = reason
        self.fields = tuple(fields)

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason, self.fields))


@dataclass(frozen=True, slots=True)
class FieldReview:
    """One field the review shows: its ancestor, upstream and campaign values."""

    field: str
    status: Status
    ancestor: Value
    upstream: Value
    local: Value = None
    #: for an ``extra:`` row, the dotted paths that differ (``card.texts.example-messages``); a path
    #: in a base layer, not the leaf, starts ``base<n>:`` (``base1:`` is the leaf's base)
    changed: tuple[str, ...] = ()

    @property
    def needs_decision(self) -> bool:
        return self.status in ("upstream", "conflict")

    @property
    def keepable(self) -> bool:
        """Whether ``keep`` can be carried by the instance (see the module docstring)."""
        if self.field == "name":
            return True
        if not self.field.startswith("facet:"):
            return False  # description, sections and payload extras: no instance-level override in this slice
        # an upstream-only change that adds a trait cannot be suppressed by an instance
        return self.status == "conflict" or self.ancestor is not None


def _overrides(instance: EntityInstance) -> dict[str, Facet]:
    return {facet.name: facet for facet in (*instance.replaced, *instance.added)}


def _row(field: str, ancestor: Value, upstream: Value, local: Value, overridden: bool) -> FieldReview | None:
    changed = ancestor != upstream
    if not changed:
        return FieldReview(field, "local", ancestor, upstream, local) if overridden else None
    if not overridden:
        return FieldReview(field, "upstream", ancestor, upstream)
    if local == upstream:
        return FieldReview(field, "converged", ancestor, upstream, local)
    return FieldReview(field, "conflict", ancestor, upstream, local)


def _same(left: object, right: object) -> bool:
    """JSON equality that keeps types apart at every depth (``true``, ``1`` and ``1.0`` differ)."""
    return JsonObject({"v": left}) == JsonObject({"v": right})


def _paths(left: JsonValue, right: JsonValue, path: str, depth: int, out: list[str]) -> None:
    if _same(left, right):
        return
    if depth < _MAX_PATH_DEPTH and isinstance(left, JsonObject) and isinstance(right, JsonObject):
        found = len(out)
        for key in sorted(set(left) | set(right)):
            _paths(left.get(key), right.get(key), f"{path}.{key}", depth + 1, out)
        if len(out) > found:
            return
    out.append(path)


def _layer_values(definition: EffectiveDefinition, read_extras: ExtrasReader) -> dict[str, tuple[JsonValue, ...]]:
    """Each uncomposed key of ``definition``: its value in every layer, leaf first (``None`` where absent)."""
    named = set(definition.uncomposed)
    layers = [node.ref for node in definition.chain]
    extras = {ref: read_extras(ref) for ref in {ref for ref, _ in named}}
    values: dict[str, tuple[JsonValue, ...]] = {}
    for key in sorted({key for _, key in named}):
        found = [extras[ref][key] if (ref, key) in named else None for ref in layers]
        while found and found[-1] is None:
            found.pop()
        values[key] = tuple(found)  # type: ignore[arg-type]
    return values


def _extra_rows(ancestor: EffectiveDefinition, upstream: EffectiveDefinition,
                read_extras: ExtrasReader | None) -> list[FieldReview]:
    if not (ancestor.uncomposed or upstream.uncomposed):
        return []
    if read_extras is None:
        raise TypeError(f"{ancestor.ref} or {upstream.ref} has uncomposed payload keys; review_fields needs "
                        "read_extras to compare them")
    old, new = _layer_values(ancestor, read_extras), _layer_values(upstream, read_extras)
    rows = []
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key, ()), new.get(key, ())
        if _same(before, after):
            continue
        changed: list[str] = []
        for depth in range(max(len(before), len(after))):
            prefix = key if depth == 0 else f"base{depth}:{key}"
            _paths(before[depth] if depth < len(before) else None, after[depth] if depth < len(after) else None,
                   prefix, 0, changed)
        rows.append(FieldReview(f"{EXTRA_PREFIX}{key}", "upstream", before or None, after or None,
                                changed=tuple(changed)))
    return rows


def review_fields(ancestor: EffectiveDefinition, upstream: EffectiveDefinition,
                  instance: EntityInstance, *, read_extras: ExtrasReader | None = None) -> tuple[FieldReview, ...]:
    """The rows of a rebase of ``instance`` from ``ancestor`` onto ``upstream``, in field order.

    ``read_extras`` gives one chain layer's payload extras; it is needed (``TypeError`` otherwise)
    when either side has uncomposed keys, which get an ``extra:<key>`` row when they differ. An
    instance has no override of extras, so such a row is always ``upstream`` and only ``take`` applies.

    Refuses (``RebaseError``): ``campaign_original`` (an instance with no source has nothing to
    rebase), ``other_entity`` (the two definitions or the instance's source are not one entity),
    ``up_to_date`` (the same revision), ``nature_changed`` (an individual became a template or back:
    a different kind of thing, not a field change).
    """
    for value, label in ((ancestor, "ancestor"), (upstream, "upstream")):
        if not isinstance(value, EffectiveDefinition):
            raise TypeError(f"the {label} must be an EffectiveDefinition, not {type(value).__name__}")
    if not isinstance(instance, EntityInstance):
        raise TypeError(f"expected an EntityInstance, not {type(instance).__name__}")
    if instance.source is None:
        raise RebaseError(f"{instance.ref} is a campaign original; it has no source to rebase", "campaign_original")
    keys = {ancestor.ref.entity_key, upstream.ref.entity_key, instance.source.entity_key}
    if len(keys) != 1:
        raise RebaseError(f"{instance.ref} ({instance.source.unpinned()}) cannot be rebased from {ancestor.ref} onto "
                          f"{upstream.ref}: a rebase stays within one entity, never a namesake", "other_entity")
    if ancestor.ref == upstream.ref:
        raise RebaseError(f"{instance.ref} is already based on {upstream.ref}", "up_to_date")
    if ancestor.nature != upstream.nature:
        raise RebaseError(f"{upstream.ref} is a {upstream.nature}, {ancestor.ref} was a {ancestor.nature}",
                          "nature_changed")
    if instance.individuality is not None and ancestor.name != upstream.name:
        # an individual's name is the given name its campaign decided (024), never the template's
        rows: list[FieldReview | None] = [
            FieldReview("name", "individual", ancestor.name, upstream.name, instance.local_name)]
    else:
        rows = [_row("name", ancestor.name, upstream.name, instance.local_name, instance.local_name is not None)]
    # description: blank composed text is absence (022)
    rows.append(_row("description", ancestor.description if ancestor.description.strip() else None,
                     upstream.description if upstream.description.strip() else None, None, False))
    old_sections, new_sections = dict(ancestor.sections), dict(upstream.sections)
    for name in sorted(set(old_sections) | set(new_sections)):
        rows.append(_row(f"section:{name}", old_sections.get(name), new_sections.get(name), None, False))
    old_facets = {facet.name: facet for facet in ancestor.traits}
    new_facets = {facet.name: facet for facet in upstream.traits}
    local = _overrides(instance)
    for name in sorted(set(old_facets) | set(new_facets) | set(local)):
        rows.append(_row(f"facet:{name}", old_facets.get(name), new_facets.get(name), local.get(name),
                         name in local))
    rows.extend(_extra_rows(ancestor, upstream, read_extras))
    return tuple(row for row in rows if row is not None)


def _check_decisions(rows: tuple[FieldReview, ...], decisions: Mapping[str, str]) -> None:
    if not isinstance(decisions, Mapping):
        raise TypeError("decisions must map a field to 'take' or 'keep'")
    by_field = {row.field: row for row in rows}
    unknown = sorted(str(key) for key in decisions
                     if key not in by_field or not by_field[key].needs_decision)  # type: ignore[index]
    if unknown:
        raise RebaseError(f"no decision is asked for {unknown}", "unknown_decision", tuple(unknown))
    invalid = sorted(field for field, value in decisions.items() if value not in DECISIONS)
    if invalid:
        raise RebaseError(f"a decision is 'take' or 'keep': {invalid}", "invalid_decision", tuple(invalid))
    undecided = tuple(row.field for row in rows if row.needs_decision and row.field not in decisions)
    if undecided:
        raise RebaseError(f"every changed field needs a decision: {list(undecided)}", "undecided", undecided)
    fixed = tuple(row.field for row in rows if decisions.get(row.field) == "keep" and not row.keepable)
    if fixed:
        raise RebaseError(f"{list(fixed)} cannot be kept by the campaign in this slice (no instance override of "
                          "authored text or payload extras, or of a trait the source adds); take it or stay pinned",
                          "not_overridable", fixed)


def rebase_instance(instance: EntityInstance, rows: tuple[FieldReview, ...], decisions: Mapping[str, str],
                    upstream: EffectiveDefinition) -> tuple[EntityInstance, EffectiveEntity]:
    """The instance after the decided rebase, and its composition over ``upstream``.

    A pinned source is re-pinned to ``upstream``; a source that follows its head stays unpinned (the
    adapter only offers the head then). Everything else of the instance (id, state, individuality,
    unknown keys) is kept. The result must compose over ``upstream`` (009's reasons propagate as
    ``EntityError``, e.g. ``state_without_facet`` when a removed ``durable`` leaves integrity behind).
    """
    _check_decisions(rows, decisions)
    if instance.source is None:  # review_fields refused it already; defensive for a hand-built row list
        raise RebaseError(f"{instance.ref} is a campaign original", "campaign_original")
    local_name = instance.local_name
    replaced = {facet.name: facet for facet in instance.replaced}
    added = {facet.name: facet for facet in instance.added}
    for row in rows:
        decision = decisions.get(row.field)
        if row.field == "name":
            if decision == "take":
                local_name = None
            elif decision == "keep" and row.status == "upstream":
                local_name = row.ancestor  # type: ignore[assignment]
            continue
        if not row.field.startswith("facet:"):
            continue  # authored text and payload extras: take inherits it, and keep was refused above
        name = row.field.split(":", 1)[1]
        if decision == "take":
            replaced.pop(name, None)
            added.pop(name, None)
            continue
        value = row.local if row.status != "upstream" else row.ancestor
        if decision is None and row.status == "local":
            continue  # upstream did not change it: the override is valid as it is
        # keep, or converged: the campaign's value, carried where the upstream lets it compose
        replaced.pop(name, None)
        added.pop(name, None)
        if row.upstream is not None:
            replaced[name] = value  # type: ignore[assignment]
        else:
            added[name] = value  # type: ignore[assignment]
    source = upstream.ref if instance.source.pinned else instance.source
    rebased = replace(instance, source=source, local_name=local_name,
                      replaced=FacetBundle(tuple(replaced.values())), added=FacetBundle(tuple(added.values())))
    return rebased, compose_instance(rebased, upstream)
