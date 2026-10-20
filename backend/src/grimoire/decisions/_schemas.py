"""Request validation and portable schema budgets for structured questions."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from .. import schemas
from . import _models
from ._models import Choice as Choice
from ._models import Item as Item
from ._models import Joint as Joint
from ._models import MultiSelect as MultiSelect
from ._models import Question as Question
from ._models import Rank as Rank

# --- validation -------------------------------------------------------------

_SEPARATORS = re.compile(r"[ -]+")


def normalise(word: str) -> str:
    """The one spelling normalisation a choice answer gets: casefolded,
    stripped, and every run of spaces or hyphens as `_`."""
    return _SEPARATORS.sub("_", word.casefold().strip())


def offerable(spelling: str) -> bool:
    """Whether `spelling` can be an option id or alias: it must survive
    `normalise` non-empty, and not as `NONE_KEY`, which is reserved for the
    native none. `_check_choice` refuses what this refuses, through this one
    function, so a builder can drop what `validate` would refuse instead of
    failing the request."""
    key = normalise(spelling)
    return bool(key) and key != _models.NONE_KEY


def _check_offers(noun: str, qid: str, options: Sequence[object]) -> None:
    """Every one of `options` an `Option` whose id and aliases are
    `offerable`, and no two of those spellings colliding once normalised: one
    rule for whatever a question offers (a choice's options, a rank's
    candidates), so the parser can always tell which one a reply named."""
    seen: dict[str, str] = {}
    for opt in options:
        if not isinstance(opt, _models.Option):
            raise _models.DecideRequestError(f"{noun} {qid!r} has an option that is not an Option")
        for spelling in (opt.id, *opt.aliases):
            if not offerable(spelling):
                raise _models.DecideRequestError(
                    f"{noun} {qid!r} cannot offer {spelling!r}: it is empty once "
                    f"normalised, or reserved")
            key = normalise(spelling)
            if key in seen:
                raise _models.DecideRequestError(
                    f"{noun} {qid!r}: {spelling!r} collides with {seen[key]!r} "
                    f"once normalised")
            seen[key] = spelling


def _check_choice(q: Choice, noun: str = "choice") -> None:
    low = _models.MIN_OPTIONS_WITH_NONE if q.allow_none else _models.MIN_OPTIONS
    if not low <= len(q.options) <= _models.MAX_OPTIONS:
        raise _models.DecideRequestError(
            f"{noun} {q.id!r} offers {len(q.options)} options; "
            f"it needs {low}-{_models.MAX_OPTIONS}")
    _check_offers(noun, q.id, q.options)
    chars = sum(len(opt.id) for opt in q.options)
    if len(q.options) > _models.ENUM_STRING_CHARS_ABOVE and chars > _models.MAX_ENUM_STRING_CHARS:
        raise _models.DecideRequestError(
            f"{noun} {q.id!r} offers {len(q.options)} options whose ids total {chars} "
            f"characters; an enum of more than {_models.ENUM_STRING_CHARS_ABOVE} values "
            f"carries at most {_models.MAX_ENUM_STRING_CHARS}")


def _check_rank(q: Rank) -> None:
    n = len(q.candidates)
    if not _models.MIN_RANK_CANDIDATES <= n <= _models.MAX_RANK_CANDIDATES:
        raise _models.DecideRequestError(
            f"rank {q.id!r} orders {n} candidates; "
            f"it needs {_models.MIN_RANK_CANDIDATES}-{_models.MAX_RANK_CANDIDATES}")
    _check_offers("rank", q.id, q.candidates)
    if q.top is not None and (isinstance(q.top, bool) or not isinstance(q.top, int)
                              or not 1 <= q.top <= n):
        raise _models.DecideRequestError(
            f"rank {q.id!r} asks for the top {q.top!r} of {n} candidates; "
            f"it needs 1-{n}, or None for all of them")


def _bound(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_select(q: MultiSelect) -> None:
    n = len(q.options)
    if not _models.MIN_SELECT_OPTIONS <= n <= _models.MAX_SELECT_OPTIONS:
        raise _models.DecideRequestError(
            f"select {q.id!r} offers {n} options; "
            f"it needs {_models.MIN_SELECT_OPTIONS}-{_models.MAX_SELECT_OPTIONS}")
    _check_offers("select", q.id, q.options)
    hi = n if q.max is None else q.max
    if not (_bound(q.min) and _bound(hi) and 0 <= q.min <= hi <= n):
        raise _models.DecideRequestError(
            f"select {q.id!r} asks for {q.min!r} to {q.max!r} of {n} options; "
            f"it needs 0 <= min <= max <= {n} (max None for all of them)")


def _check_joint_option(q: Joint, opt: object) -> None:
    if not isinstance(opt, _models.Option):
        raise _models.DecideRequestError(f"joint {q.id!r} has an option that is not an Option")
    if _models.JOINT_SEP in opt.id:
        raise _models.DecideRequestError(
            f"joint {q.id!r} cannot offer {opt.id!r}: it holds {_models.JOINT_SEP!r}, which "
            f"joins a pair's key")
    if opt.aliases:
        raise _models.DecideRequestError(
            f"joint {q.id!r} cannot alias {opt.id!r}: pairing aliases would multiply them")


def _check_joint(q: Joint) -> None:
    """A joint's own rules -- the separator, no aliases, a `tails` keyed by
    its heads, each once -- then the flattened choice's (`_check_choice`),
    which holds the pair count, offerability and collisions."""
    for head in q.heads:
        _check_joint_option(q, head)
    heads = {head.id for head in q.heads}
    seen: set[str] = set()
    for entry in q.tails if isinstance(q.tails, tuple) else (None,):
        if not (isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[0], str)
                and isinstance(entry[1], tuple)):
            raise _models.DecideRequestError(
                f"joint {q.id!r}'s tails are not (head id, targets) pairs")
        head_id, targets = entry
        if head_id not in heads:
            raise _models.DecideRequestError(f"joint {q.id!r} lists targets for {head_id!r}, "
                                     f"which is not one of its heads")
        if head_id in seen:
            raise _models.DecideRequestError(f"joint {q.id!r} lists {head_id!r}'s targets twice")
        seen.add(head_id)
        for target in targets:
            _check_joint_option(q, target)
    _check_choice(q.choice, "joint")


def _check_question(q: object, index: int) -> None:
    if not isinstance(q, (_models.Predicate, _models.Choice, _models.Score, _models.Rank, _models.MultiSelect, _models.Joint)):
        raise _models.DecideRequestError(f"item {index} has a question of unknown type")
    if not q.id.strip():
        raise _models.DecideRequestError(f"item {index} has a question with an empty id")
    if q.id.isdigit() or q.id in _models.RESERVED_IDS:
        raise _models.DecideRequestError(f"question id {q.id!r} is reserved")
    if isinstance(q, _models.Choice):
        _check_choice(q)
    elif isinstance(q, _models.Rank):
        _check_rank(q)
    elif isinstance(q, _models.MultiSelect):
        _check_select(q)
    elif isinstance(q, _models.Joint):
        _check_joint(q)
    elif isinstance(q, _models.Score) and not _models.MIN_LEVELS <= len(q.levels) <= _models.MAX_LEVELS:
        raise _models.DecideRequestError(
            f"score {q.id!r} has {len(q.levels)} levels; "
            f"it needs {_models.MIN_LEVELS}-{_models.MAX_LEVELS}")


def _enum_count(q: Question) -> int:
    if isinstance(q, _models.Choice):
        return len(q.options)
    if isinstance(q, _models.Score):
        return len(q.levels)
    if isinstance(q, _models.Rank):
        return len(q.candidates)
    if isinstance(q, _models.MultiSelect):
        return len(q.options)
    if isinstance(q, _models.Joint):
        return len(q.choice.options)
    return 0


def enum_values(item: Item) -> int:
    """How many enum values `item` adds to a batch schema: each choice's
    options, each score's levels, each rank's candidates, each select's
    options and each joint's legal pairs (a predicate is a plain boolean, and
    a nullable question's null branch is a type, not a value)."""
    return sum(_enum_count(q) for q in item.questions)


def validate(items: Sequence[Item]) -> None:
    """Refuse a request the schema or the parser could not answer unambiguously.

    Raises `DecideRequestError` for: no items; an item with no questions; a
    question id that is empty, all digits, reserved or repeated within its item;
    an option, level or candidate count out of bounds; a rank's `top` outside
    1 to its candidate count; an option or candidate id or alias `offerable`
    refuses; any two of a question's ids or aliases that collide once
    normalised; a
    choice of more than `ENUM_STRING_CHARS_ABOVE` options whose ids total more
    than `MAX_ENUM_STRING_CHARS` characters; and an item whose own schema no
    chunk could carry -- more than `MAX_ENUM_VALUES` enum values, more than
    `MAX_SCHEMA_STRING_CHARS` characters (`schema_chars`) or more than
    `MAX_SCHEMA_PROPERTIES` properties (`schema_properties`). The schema limits
    are strict mode's, refused whatever the backend.
    """
    if not items:
        raise _models.DecideRequestError("a decision needs at least one item")
    for index, item in enumerate(items):
        if not isinstance(item, _models.Item):
            raise _models.DecideRequestError(f"item {index} is not an Item")
        if not item.questions:
            raise _models.DecideRequestError(f"item {index} asks no questions")
        ids: set[str] = set()
        for q in item.questions:
            _check_question(q, index)
            if q.id in ids:
                raise _models.DecideRequestError(f"item {index} asks {q.id!r} twice")
            ids.add(q.id)
        if enum_values(item) > _models.MAX_ENUM_VALUES:
            raise _models.DecideRequestError(
                f"item {index} offers {enum_values(item)} enum values; one call "
                f"carries at most {_models.MAX_ENUM_VALUES}")
        properties, chars = schemas.tally(schema([item], explain=True))
        if chars > _models.MAX_SCHEMA_STRING_CHARS:
            raise _models.DecideRequestError(
                f"item {index}'s schema holds {chars} characters of names and enum "
                f"values; one call carries at most {_models.MAX_SCHEMA_STRING_CHARS}")
        if properties > _models.MAX_SCHEMA_PROPERTIES:
            raise _models.DecideRequestError(
                f"item {index}'s schema holds {properties} properties; one call "
                f"carries at most {_models.MAX_SCHEMA_PROPERTIES}")


# --- schema -----------------------------------------------------------------

def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    """A closed object: every property required, nothing else allowed (strict
    mode requires both)."""
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def _nullable(node: dict[str, Any], allow_none: bool) -> dict[str, Any]:
    return {"anyOf": [node, {"type": "null"}]} if allow_none else node


def _question_schema(q: Question) -> dict[str, Any]:
    if isinstance(q, _models.Joint):
        # A string enum of the legal pairs' keys: it admits exactly the legal
        # pairs, where `anyOf` objects per head would need `const` stand-ins
        # and multiply properties for nothing.
        q = q.choice
    if isinstance(q, _models.Predicate):
        return {"type": "boolean"}
    if isinstance(q, _models.Choice):
        return _nullable({"type": "string", "enum": [opt.id for opt in q.options]},
                         q.allow_none)
    if isinstance(q, (_models.Rank, _models.MultiSelect)):
        # No `minItems`, `maxItems` or `uniqueItems`: the parser holds the
        # count and uniqueness (the module docstring).
        offered = q.candidates if isinstance(q, _models.Rank) else q.options
        return _nullable({"type": "array", "items": {
            "type": "string", "enum": [opt.id for opt in offered]}}, q.allow_none)
    return {"type": "integer", "enum": list(range(len(q.levels)))}


def schema(items: Sequence[Item], *, explain: bool) -> dict[str, Any]:
    """The JSON Schema of one batch's reply: items keyed by index, each an
    object of `answers` (and `rationale` when `explain`)."""
    def item_schema(item: Item) -> dict[str, Any]:
        props: dict[str, Any] = {
            "answers": _obj({q.id: _question_schema(q) for q in item.questions})}
        if explain:
            props["rationale"] = {"type": "string"}
        return _obj(props)

    return _obj({str(i): item_schema(item) for i, item in enumerate(items)})


def schema_chars(items: Sequence[Item]) -> int:
    """The characters strict mode's 120,000 budget counts in `items`' batch
    schema: every property name and every string enum value. Counted with
    `explain=True`, the worst case, so `rationale` counts whether asked or
    not; integer enums (a score's levels) count nothing."""
    return schemas.tally(schema(items, explain=True))[1]


def schema_properties(items: Sequence[Item]) -> int:
    """The object properties strict mode's 5,000 limit counts in `items`' batch
    schema, with `explain=True`: each item's index, its `answers` and
    `rationale`, and one per question."""
    return schemas.tally(schema(items, explain=True))[0]


def chunks(items: Sequence[Item], size: int = _models.MAX_ITEMS_PER_CALL,
           budget: int = _models.MAX_ENUM_VALUES, chars: int = _models.MAX_SCHEMA_STRING_CHARS,
           properties: int = _models.MAX_SCHEMA_PROPERTIES) -> list[tuple[int, tuple[Item, ...]]]:
    """`(offset, chunk)` pairs covering `items` in order, each at most `size`
    items, at most `budget` enum values (`enum_values`), at most `chars`
    schema characters (`schema_chars`) and at most `properties` schema
    properties (`schema_properties`) -- except an item that alone exceeds one
    of them, which is a chunk of its own (`validate` refuses one before
    `decide` chunks)."""
    if size < 1:
        raise ValueError("a chunk holds at least one item")
    out: list[tuple[int, tuple[Item, ...]]] = []
    held: list[Item] = []
    start = values = 0
    for index, item in enumerate(items):
        count = enum_values(item)
        if held:
            props, text = schemas.tally(schema([*held, item], explain=True))
            if (len(held) == size or values + count > budget or text > chars
                    or props > properties):
                out.append((start, tuple(held)))
                start, held, values = index, [], 0
        held.append(item)
        values += count
    if held:
        out.append((start, tuple(held)))
    return out
