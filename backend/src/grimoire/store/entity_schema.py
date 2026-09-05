"""Per-kind typed field descriptors (#37, #222, #221).

Fields are extra string scalars in the entity's frontmatter, so entity_hash,
sync conflict detection, and campaign copy-on-write cover them for free. The
frontend mirrors this table as ENTITY_FIELDS in frontend/src/api/types.ts --
keep the two in sync. Four widgets: `text`, `ref` (#222), `number` and
`choice` (#221).

## The spec is the constraint

A `number` carries an optional `min`/`max`; a `choice` carries either a static
`options` tuple or the name of a `source` in `OPTION_SOURCES` (a list that is
only known at runtime -- the climates, which the user can add to). Both sides
read the same keys: the save boundary refuses what the spec does not admit
(`valid_value`), and the form renders the control the spec implies -- a
bounded number input, a picker over exactly those options -- rather than a
text box whose rule lives in Python it cannot see. Before this, `climate` and
`persistence` were text boxes with bespoke validators: the typo was reported,
but only after Save, and nothing offered the right spelling.

Game *mechanics* -- stat blocks, resources, tracks -- are not fields here and
never will be: they are sheets (`store/sheets`), typed per sheet type by the
campaign's bound module and stored campaign-side, so a world record stays
mechanics-agnostic and the same creature can be statted differently in two
campaigns. The descriptor holds what is true of the record in every campaign.

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

The comma is the list separator, so **an id containing one cannot be named by a
ref** -- `locations:salt,march` reads as two refs, and `REF_DELIMITER` is what
both sides check against. `slugify` reduces everything outside `[a-z0-9]` to a
dash, so nothing this app creates can hit it; a hand-authored or imported
`salt,march.md` can, and `referenceable` is what keeps the picker from offering
a candidate that could never be saved. Inherited from `owners:`, which has
always had the same grammar and the same hole.

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

## A key this table claims may already hold something else

`holder`, `leader`, `headquarters` and `habitat` are ordinary words, and until
they were declared here they were unrecognised frontmatter -- preserved across
every edit and shown to nobody. A store predating this table can therefore
carry `holder: Mara`, which is not a ref and never will be.

Such a value is **left exactly where it is**. It is refused only if something
tries to *set* it: `EntityEditor` sends the fields it changed rather than the
whole set, so an untouched legacy value is never resubmitted and never
rejected, and an unrelated body edit saves as it always did. The reader shows
it as an unresolved ref, which is what it is.

Validating what a request changes rather than what a record holds is the rule
this shares with the two paragraphs above, and the failure it avoids is the
same one each time: a stored value the boundary refuses is a record nobody can
edit any more, which is a far worse outcome than the bad value itself.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from . import climates
from .paths import safe_id

TEXT, REF, NUMBER, CHOICE = "text", "ref", "number", "choice"

#: Where a `choice` spec's options come from when they are not a literal
#: tuple: the user-extensible lists, read fresh each time so a climate saved a
#: moment ago is offered (and accepted) at once.
OPTION_SOURCES: dict[str, Callable[[], list[str]]] = {
    "climates": lambda: [c["id"] for c in climates.list_climates()],
}

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
        {"key": "climate", "label": "Climate", "widget": CHOICE, "source": "climates"},
        # A probability: the resolver (`store/weather`) reads it as the chance
        # that a day's weather carries over, and falls back silently outside
        # 0..1, which is exactly why the bound is declared here.
        {"key": "persistence", "label": "Weather persistence", "widget": NUMBER,
         "min": 0, "max": 1},
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


REF_DELIMITER = ","


def referenceable(eid: str) -> bool:
    """Can a ref name this id at all?

    Three rules, and `safe_id` is only the first. It is about what a *resolver*
    may open, so it permits characters a stored ref cannot survive:

    - `safe_id`: no separator, no colon, no traversal, no trailing dot or space.
    - No `REF_DELIMITER`: the field is a comma-separated list, so an id with a
      comma in it reads back as two refs.
    - **Nothing `str.splitlines` treats as a line boundary.** A ref is written
      into a single-line frontmatter scalar, and such a character does not
      survive the round trip: `dump_frontmatter` puts it straight into the
      value and `parse_frontmatter` -- which splits with `splitlines` -- reads
      the rest of the line as the whole of it, so `characters:a\nb` is stored,
      reported saved, and reads back as the truncated `'characters:a`, stray
      quote included and with the record's own body boundary one line further
      down than it was. A save that reports success and corrupts the file is
      the worst failure on this surface.

      **`splitlines` itself, not a list of characters.** That is the point of
      writing it this way: `\n` and `\r` are the obvious two, and the first
      draft here checked exactly those -- leaving vertical tab, form feed, the
      three file/group/record separators, NEL and the two Unicode line/
      paragraph separators all accepted and all corrupting. Eight ways to get
      it wrong, and the only way not to is to ask the same question the parser
      asks. A tab, by contrast, round-trips intact and is accepted: the rule is
      exactly "what the frontmatter writer cannot carry", held against the real
      writer by `test_referenceable_agrees_with_what_frontmatter_can_carry`
      rather than asserted.

    Said once, here, so the picker and the save boundary cannot drift: a rule
    living in only one of them means a candidate the UI offers and the backend
    refuses, with nothing anywhere saying why.

    (`safe_id` itself still accepts a newline. That is a wider question than
    refs -- it is the id of a real directory somebody could create -- and it is
    left where it is rather than tightened from here.)
    """
    return (safe_id(eid) and REF_DELIMITER not in eid
            and eid.splitlines() == [eid])


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
    return [r.strip() for r in value.split(REF_DELIMITER) if r.strip()]


def field_keys(kind: str) -> tuple[str, ...]:
    return tuple(f["key"] for f in FIELDS.get(kind, ()))


def invalid_keys(kind: str, fields: dict) -> list[str]:
    allowed = set(field_keys(kind))
    return sorted(k for k in fields if k not in allowed)


def choice_options(spec: dict[str, Any]) -> list[str]:
    """The values a `choice` spec admits, in the order a picker should offer them."""
    if "options" in spec:
        return list(spec["options"])
    return OPTION_SOURCES[spec["source"]]()


def _valid_number(spec: dict[str, Any], value: object) -> bool:
    # bool first: `float(True)` is 1.0, so a JSON `true` would validate, but the
    # store writes it back as the string "True", which the resolver cannot parse
    # and silently replaces with the climate's own persistence. That is a save
    # that reports success and never takes effect -- the exact failure this
    # boundary exists to prevent.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        parsed = float(value)
    except (ValueError, OverflowError):
        # OverflowError: `fields` is an untyped dict, so a JSON integer like
        # 10**1000 arrives as a Python int with no float value. Uncaught it
        # escapes _check_fields as a 500 instead of the 400 it is.
        return False
    if not math.isfinite(parsed):
        return False
    if "min" in spec and parsed < spec["min"]:
        return False
    return not ("max" in spec and parsed > spec["max"])


def valid_value(spec: dict[str, Any], value: object) -> bool:
    """Does `value` satisfy `spec`? Structural for every widget, and for a
    `choice` also membership -- the one check that is a lookup rather than a
    parse. Empties are not values and are decided by the caller.

    A `text` value must be a string: `dump_frontmatter` would stringify
    anything else and the file would carry that spelling, which is the same
    save-reports-success-and-stores-something-else failure the number check
    guards against, one widget over.
    """
    widget = spec.get("widget", TEXT)
    if widget == REF:
        return _valid_ref_value(spec, value)
    if widget == NUMBER:
        return _valid_number(spec, value)
    if widget == CHOICE:
        return isinstance(value, str) and value in choice_options(spec)
    return isinstance(value, str)


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
    specs = {f["key"]: f for f in FIELDS.get(kind, ())}
    bad = []
    for key, value in (fields or {}).items():
        if value is None or value == "":
            continue
        spec = specs.get(key)
        if spec is not None and not valid_value(spec, value):
            bad.append(key)
    return sorted(bad)


def _valid_ref_value(spec: dict[str, Any], value: object) -> bool:
    """Is `value` a ref (or list of refs) this spec would accept?

    Structural only -- see the module docstring on why existence is not asked.
    What it does ask is everything the picker guarantees and a hand-written
    payload might not: the value is a string, it parses to at least one ref, a
    single-valued field got exactly one, and each ref names an accepted kind
    with an id `paths.safe_id` would let a resolver open.

    `referenceable` is the load-bearing half. `safe_id` under it rejects the
    colon, so `<kind>:<id>` splits unambiguously on the FIRST one and
    `characters:a:b` cannot be read as an id containing a separator, and it
    rejects the traversal that would otherwise let a stored ref name a path
    outside the store the day something resolves one; the comma rule on top is
    what keeps the grammar and the id space agreeing.
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
        if not sep or head not in spec["kinds"] or not referenceable(eid):
            return False
    return True
