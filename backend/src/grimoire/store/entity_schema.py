"""Per-kind typed field descriptors (#37, #222).

Fields are extra string scalars in the entity's frontmatter, so entity_hash,
sync conflict detection, and campaign copy-on-write cover them for free. The
frontend mirrors this table as ENTITY_FIELDS in frontend/src/api/client.ts —
keep the two in sync. Two widgets: `text`, and `ref` (#222). Game mechanics
are still deferred (#221).

## Ref-valued fields

A `ref` field names other records, in the `<kind>:<id>` spelling `owners:`
already uses -- one ref, or a comma-separated list when the spec says `multi`.
It stays a plain frontmatter string for the reason above: the moment a field
became a YAML list, every guarantee in the first paragraph would need its own
answer.

Each spec carries the `kinds` it may name, and that is the whole of what makes
the widget a *picker* rather than a text box: the frontend offers exactly those
kinds' records, and this module refuses anything else at the save boundary. A
group is led by a person and headquartered in a place, and a save that says
otherwise is a bug in whatever produced it.

## Dangling refs: deleting a target does not rewrite the refs that name it

Deliberate, and the alternative is worse in three separate ways:

- **Scope.** A ref lives in a record that may be the world's, while the delete
  may be a campaign's (`overlay.add_deleted` tombstones the world's copy
  without touching it). Scrubbing the world's text for one campaign's decision
  edits a record every other campaign shares; scrubbing campaign-side
  materializes a copy of every referring record and diverges it from the
  library -- turning one delete into a pile of sync conflicts on records nobody
  edited.
- **Recovery.** Ids are handed out by slug (`entities.create_entity`), so a
  record re-created under its old name takes its id back and every ref left
  alone starts resolving again. A scrubbed ref is gone for good.
- **Silence.** A scrubbed field looks like a field nobody ever filled in. A ref
  that no longer resolves says the holder is *missing*, which is the thing the
  user needs to see -- so the reader renders it as a dangling chip rather than
  dropping it.

A *reclassify* is the other case and does rewrite: the record still exists and
only its kind changed, so `entities.rewrite_ref_fields` repoints these fields
exactly as `rewrite_owner_refs` repoints `owners:`.

Existence is not checked at the save boundary either, for the first reason
above plus one more: it would make a save depend on the order two records were
written in. Format is checkable and stable; existence is neither.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from . import climates
from .paths import safe_id

TEXT, REF = "text", "ref"

# The kinds a `ref` field may name. Wider than `entities.ENTITY_KINDS` because
# the interesting refs point at actors -- and spelled out here rather than
# imported so this module stays a leaf that `entities` may import (the
# rewriter there needs to know which fields are refs). `test_entity_schema`
# holds the list against `entities.ENTITY_KINDS` + the actor kinds.
REF_KINDS: tuple[str, ...] = (
    "characters", "pcs", "locations", "lore", "items", "groups", "creatures",
)

# `dict[str, Any]` rather than a TypedDict: the values are heterogeneous
# (`kinds` is a tuple, `multi` a bool) and every reader below asks for one key
# at a time. The shape is held by `test_entity_schema.py`, which is stricter
# than a total=False TypedDict would be anyway -- it checks that a text field
# carries no `kinds`, which no type can say.
FIELDS: dict[str, tuple[dict[str, Any], ...]] = {
    "locations": (
        {"key": "climate", "label": "Climate", "widget": TEXT},
        {"key": "persistence", "label": "Weather persistence", "widget": TEXT},
        {"key": "weather_zone", "label": "Weather zone", "widget": TEXT},
    ),
    "items": (
        {"key": "item_type", "label": "Type", "widget": TEXT},
        {"key": "rarity", "label": "Rarity", "widget": TEXT},
        # Who or what has it. Places included: an item sits somewhere at least
        # as often as somebody carries it.
        {"key": "holder", "label": "Held by", "widget": REF,
         "kinds": ("characters", "pcs", "groups", "locations")},
    ),
    "groups": (
        {"key": "group_type", "label": "Type", "widget": TEXT},
        {"key": "leader", "label": "Leader", "widget": REF,
         "kinds": ("characters", "pcs")},
        {"key": "headquarters", "label": "Headquarters", "widget": REF,
         "kinds": ("locations",)},
    ),
    "creatures": (
        {"key": "creature_type", "label": "Type", "widget": TEXT},
        {"key": "threat", "label": "Threat", "widget": TEXT},
        # Plural where the others are singular, and that is the point of
        # carrying `multi` at all: a thing ranges over places, it is not kept
        # in one.
        {"key": "habitat", "label": "Habitat", "widget": REF,
         "kinds": ("locations",), "multi": True},
    ),
}


def ref_fields(kind: str) -> tuple[dict[str, Any], ...]:
    """The `ref` specs declared for `kind`, in declaration order."""
    return tuple(f for f in FIELDS.get(kind, ()) if f.get("widget") == REF)


def parse_refs(value: object) -> list[str]:
    """A ref field's stored string as the refs it names.

    The same split-strip-filter `entities.owner_refs` does, and deliberately
    the same spelling: one comma-separated line of `<kind>:<id>`, so a reader
    that can follow an owner can follow one of these without learning a second
    format. Non-strings answer empty rather than raising -- frontmatter is
    hand-editable, and a rewriter sweeping every record must not die on one
    file where somebody typed a YAML list.
    """
    if not isinstance(value, str):
        return []
    return [r.strip() for r in value.split(",") if r.strip()]


def field_keys(kind: str) -> tuple[str, ...]:
    return tuple(f["key"] for f in FIELDS.get(kind, ()))


def invalid_keys(kind: str, fields: dict) -> list[str]:
    allowed = set(field_keys(kind))
    return sorted(k for k in fields if k not in allowed)


def _valid_climate(value: str) -> bool:
    return climates.get(value) is not None


def _valid_persistence(value: str) -> bool:
    # bool first: `float(True)` is 1.0, so a JSON `true` would validate, but the
    # store writes it back as the string "True", which the resolver cannot parse
    # and silently replaces with the climate's own persistence. That is a save
    # that reports success and never takes effect — the exact failure this
    # boundary exists to prevent.
    if isinstance(value, bool):
        return False
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError: `fields` is an untyped dict, so a JSON integer like
        # 10**1000 arrives as a Python int with no float value. Uncaught it
        # escapes _check_fields as a 500 instead of the 400 it is.
        return False
    return math.isfinite(parsed) and 0.0 <= parsed <= 1.0


# Per-field value checks. Names are validated for every kind by invalid_keys;
# only fields listed here have their contents checked as well.
VALIDATORS: dict[str, dict[str, Callable[[str], bool]]] = {
    "locations": {"climate": _valid_climate, "persistence": _valid_persistence},
}


def invalid_values(kind: str, fields: dict) -> list[str]:
    """Field keys whose values fail their check.

    Leniency is right in the turn loop and wrong here. The resolver falls back
    silently so a bad value can never take a turn down, which means the save
    boundary is the only place a typo can be reported at all: without this,
    `climate: "temperate-costal"` saves cleanly, weather resolves from a
    climate the user did not choose, and nothing anywhere says so.

    An empty string is "clear this field", not a value: `EntityEditor` sends
    `""` for a field the user never set, and `entities.update_entity` removes
    empties — but only *after* route validation, so they must be skipped here
    or every ordinary location save is rejected.
    """
    checks = VALIDATORS.get(kind, {})
    refs = {f["key"]: f for f in ref_fields(kind)}
    bad = []
    for key, value in (fields or {}).items():
        if value is None or value == "":
            continue
        spec = refs.get(key)
        if spec is not None and not _valid_ref_value(spec, value):
            bad.append(key)
            continue
        check = checks.get(key)
        if check and not check(value):
            bad.append(key)
    return sorted(bad)


def _valid_ref_value(spec: dict[str, Any], value: object) -> bool:
    """Is `value` a ref (or list of refs) this spec would accept?

    Structural only -- see the module docstring on why existence is not asked.
    What it does ask is everything the picker guarantees and a hand-written
    payload might not: the value is a string, it parses to at least one ref, a
    single-valued field got exactly one, and each ref names an accepted kind
    with an id `paths.safe_id` would let a resolver open.

    `safe_id` is the load-bearing half. It rejects the colon, so `<kind>:<id>`
    splits unambiguously on the FIRST one and `characters:a:b` cannot be read
    as an id containing a separator; and it rejects the traversal that would
    otherwise let a stored ref name a path outside the store the day something
    resolves one.
    """
    if not isinstance(value, str):
        return False
    refs = parse_refs(value)
    if not refs:
        return False               # a comma with nothing either side of it
    if len(refs) > 1 and not spec.get("multi"):
        return False
    for ref in refs:
        head, sep, eid = ref.partition(":")
        if not sep or head not in spec["kinds"] or not safe_id(eid):
            return False
    return True
