"""The decision contract: what `inference.decide` is asked, and what it answers.

A decision is a closed question about a piece of text -- is this scene over,
which of these characters speaks next, how far has this reply drifted from its
voice -- answered by a value drawn from a set the caller fixed in advance,
never by prose a call site has to pick apart. This module holds that contract
and everything about it that is pure: the request and result types, the
request's validation, the JSON Schema of one batch, the tolerant parser that
reads a reply back, and the chunking of a long batch.

It is a gateway leaf on purpose, and imports nothing from the package (spec
§7.4, ruling 15): the operation itself is `grimoire.inference`, and slice H's
native adapters are gateway modules that must normalise into these types
without reaching the store.

Three rules the rest of the module follows:

- **An answer nobody gave is `None` with a reason, never a default** (§12). The
  reasons are a closed vocabulary (`REASONS`); `unreadable` may carry a
  `detail` sub-reason, which is not a new reason. There are three details:
  `NOT_AN_OPTION` (a choice answered with a value naming no option),
  `NO_OBJECT` (the reply held no JSON object at all) and `NO_ITEM` (it held
  one, but nothing in it reads as this item). `was_read` says which side of
  that line an answer is on.
- **The schema stays inside what both providers document.** OpenAI's strict
  mode and Anthropic's `output_config.format` accept different subsets of JSON
  Schema, so a batch schema uses only `type` (object, string, boolean,
  integer, null), `enum`, `anyOf`, `required`, `properties` and
  `additionalProperties: false`. Anthropic refuses numeric bounds, so a score
  is an integer `enum`; a nullable choice is `anyOf` with a null branch, never
  a type array.
- **The parser never raises.** It answers every item it was handed, so one
  unreadable reply costs the answers it held and nothing more.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

#: Why an answer is `None`. Closed: a backend that cannot say which of these
#: happened says `unreadable` rather than adding a fifth.
REASONS = ("unreadable", "refused", "abstained", "error")

#: How a decision was answered: through `generate(schema=)` and the parser
#: below, or by a provider's own decisions endpoint (slice H).
BACKENDS = ("structured", "native")

#: The `detail`s of an `unreadable` answer (none is a reason). `NOT_AN_OPTION`:
#: a choice answered with a value that is present, not null, and names no
#: option.
NOT_AN_OPTION = "not_an_option"

#: The reply held no JSON object at all (`find_object` returned None); set on
#: every answer of every item in it.
NO_OBJECT = "no_object"

#: The reply held an object, but nothing in it reads as this item: no index key
#: and no unwrapped or flattened read applies, the entry is not an object, or
#: its `answers` is not an object. Set on every answer of that item. (A question
#: missing from an item that *was* read, and a null on a choice without
#: `allow_none`, keep no detail.)
NO_ITEM = "no_item"

MIN_OPTIONS, MAX_OPTIONS = 2, 255
#: A nullable choice has two possible answers with one option (it, or null).
MIN_OPTIONS_WITH_NONE = 1
MIN_LEVELS, MAX_LEVELS = 2, 10

#: Items per structured call (ruling 16). Each item carries its own transcript
#: context, so one call's prompt grows linearly with the batch, and one
#: unreadable reply loses every item in its chunk; eight bounds both. Argued
#: from that structure alone, and to be tuned against real prompts later --
#: never against a measured library.
MAX_ITEMS_PER_CALL = 8

#: Enum values one structured call's schema may hold, summed over every enum
#: in it (a choice's options, a score's levels). OpenAI's strict mode documents
#: "up to 1000 enum values across all enum properties" (Structured Outputs,
#: "Supported schemas", checked 2026-10-08), and Anthropic documents no lower
#: one, so a batch is chunked under this as well as under
#: `MAX_ITEMS_PER_CALL`, and an item that alone exceeds it is refused.
MAX_ENUM_VALUES = 1000

#: Question ids an item's object already uses for itself. Refusing them (and
#: all-digit ids, which are item indices) keeps the wrapped, unwrapped and
#: flattened reply shapes unambiguous.
RESERVED_IDS = ("answers", "rationale")

#: The keys the flattened single-item shape also reads as the rationale, in
#: order, after `rationale` itself: what the legacy one-call prompts named it
#: (voice drift's corrective was `note`, scene-break's reason `reason`), so a
#: model answering in that style keeps what it said. The flattened shape's
#: alone -- the wrapped and unwrapped shapes are the schema's, which names
#: `rationale` -- and never a key the item asks a question under.
LEGACY_RATIONALE_KEYS = ("note", "reason")


class DecideRequestError(ValueError):
    """A request `decide` refuses before anything is sent."""


@dataclass(frozen=True)
class Option:
    """One answer a choice offers. `aliases` are other spellings the structured
    parser accepts for it (after `normalise`); native backends ignore them."""

    id: str
    description: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Predicate:
    """A yes/no question; answered with a `bool`."""

    id: str
    instructions: str


@dataclass(frozen=True)
class Choice:
    """Pick one option; answered with its `id`. With `allow_none`, an explicit
    null is a legitimate answer (`None`, reason `abstained`)."""

    id: str
    instructions: str
    options: tuple[Option, ...]
    allow_none: bool = False


@dataclass(frozen=True)
class Score:
    """Place the text on an ordered scale; answered with a level's index."""

    id: str
    instructions: str
    levels: tuple[str, ...]


Question = Predicate | Choice | Score


@dataclass(frozen=True)
class Item:
    """One thing to decide about: its own `context`, and the questions asked of
    it, in order."""

    context: str
    questions: tuple[Question, ...]


@dataclass(frozen=True)
class Answer:
    """One question's answer.

    `reason` is set if and only if `answer is None`, and is one of `REASONS`.
    `detail` is set only beside `reason == "unreadable"`, and is one of
    `NOT_AN_OPTION`, `NO_OBJECT` or `NO_ITEM` (none of them is a reason).
    `probability` and
    `distribution` are what a backend actually reported; a missing one stays
    missing, and nothing computes a stand-in.
    """

    answer: bool | str | int | None
    reason: str = ""
    probability: float | None = None
    distribution: dict[str, float] | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if (self.answer is None) != bool(self.reason):
            raise ValueError("an answer carries a reason exactly when it is None")
        if self.reason and self.reason not in REASONS:
            raise ValueError(f"unknown reason {self.reason!r}")
        if self.detail and self.reason != "unreadable":
            raise ValueError("a detail qualifies an unreadable answer only")


@dataclass(frozen=True)
class ItemResult:
    """One item's answers, keyed by question id in question order, and its
    rationale (empty unless one was asked for and given)."""

    answers: dict[str, Answer]
    rationale: str = ""


@dataclass(frozen=True)
class Decision:
    """What `decide` returns: one `ItemResult` per item, in input order, the
    backend that answered (one of `BACKENDS`), the selection that answered,
    and each call's usage row."""

    items: tuple[ItemResult, ...]
    backend: str
    provider: str = ""
    model: str = ""
    usage: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.backend not in BACKENDS:
            raise ValueError(f"unknown backend {self.backend!r}")


# --- validation -------------------------------------------------------------

_SEPARATORS = re.compile(r"[ -]+")


def normalise(word: str) -> str:
    """The one spelling normalisation a choice answer gets: casefolded,
    stripped, and every run of spaces or hyphens as `_`."""
    return _SEPARATORS.sub("_", word.casefold().strip())


def offerable(spelling: str) -> bool:
    """Whether `spelling` can be an option id or alias: it must survive
    `normalise` non-empty. `_check_choice` refuses what this refuses, through
    this one function, so a builder can drop what `validate` would refuse
    instead of failing the request."""
    return bool(normalise(spelling))


def _check_choice(q: Choice) -> None:
    low = MIN_OPTIONS_WITH_NONE if q.allow_none else MIN_OPTIONS
    if not low <= len(q.options) <= MAX_OPTIONS:
        raise DecideRequestError(
            f"choice {q.id!r} offers {len(q.options)} options; "
            f"it needs {low}-{MAX_OPTIONS}")
    seen: dict[str, str] = {}
    for opt in q.options:
        if not isinstance(opt, Option):
            raise DecideRequestError(f"choice {q.id!r} has an option that is not an Option")
        if not offerable(opt.id):
            raise DecideRequestError(f"choice {q.id!r} has an empty option id")
        for spelling in (opt.id, *opt.aliases):
            if not offerable(spelling):
                raise DecideRequestError(f"option {opt.id!r} has an empty alias")
            key = normalise(spelling)
            if key in seen:
                raise DecideRequestError(
                    f"choice {q.id!r}: {spelling!r} collides with {seen[key]!r} "
                    f"once normalised")
            seen[key] = spelling


def _check_question(q: object, index: int) -> None:
    if not isinstance(q, (Predicate, Choice, Score)):
        raise DecideRequestError(f"item {index} has a question of unknown type")
    if not q.id.strip():
        raise DecideRequestError(f"item {index} has a question with an empty id")
    if q.id.isdigit() or q.id in RESERVED_IDS:
        raise DecideRequestError(f"question id {q.id!r} is reserved")
    if isinstance(q, Choice):
        _check_choice(q)
    elif isinstance(q, Score) and not MIN_LEVELS <= len(q.levels) <= MAX_LEVELS:
        raise DecideRequestError(
            f"score {q.id!r} has {len(q.levels)} levels; "
            f"it needs {MIN_LEVELS}-{MAX_LEVELS}")


def enum_values(item: Item) -> int:
    """How many enum values `item` adds to a batch schema: each choice's
    options and each score's levels (a predicate is a plain boolean, and a
    nullable choice's null branch is a type, not a value)."""
    return sum(len(q.options) if isinstance(q, Choice)
               else len(q.levels) if isinstance(q, Score) else 0
               for q in item.questions)


def validate(items: Sequence[Item]) -> None:
    """Refuse a request the schema or the parser could not answer unambiguously.

    Raises `DecideRequestError` for: no items; an item with no questions; a
    question id that is empty, all digits, reserved or repeated within its item;
    an option or level count out of bounds; an empty option id; any two
    option ids or aliases that collide once normalised; and an item whose own
    enum values exceed `MAX_ENUM_VALUES`, which no chunk could carry.
    """
    if not items:
        raise DecideRequestError("a decision needs at least one item")
    for index, item in enumerate(items):
        if not isinstance(item, Item):
            raise DecideRequestError(f"item {index} is not an Item")
        if not item.questions:
            raise DecideRequestError(f"item {index} asks no questions")
        ids: set[str] = set()
        for q in item.questions:
            _check_question(q, index)
            if q.id in ids:
                raise DecideRequestError(f"item {index} asks {q.id!r} twice")
            ids.add(q.id)
        if enum_values(item) > MAX_ENUM_VALUES:
            raise DecideRequestError(
                f"item {index} offers {enum_values(item)} enum values; one call "
                f"carries at most {MAX_ENUM_VALUES}")


# --- schema -----------------------------------------------------------------

def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    """A closed object: every property required, nothing else allowed (strict
    mode requires both)."""
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def _question_schema(q: Question) -> dict[str, Any]:
    if isinstance(q, Predicate):
        return {"type": "boolean"}
    if isinstance(q, Choice):
        ids = {"type": "string", "enum": [opt.id for opt in q.options]}
        return {"anyOf": [ids, {"type": "null"}]} if q.allow_none else ids
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


# --- parse ------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?[ \t]*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def _first_wins(pairs: list[tuple[Any, Any]]) -> dict[Any, Any]:
    """An object that keeps a key's first occurrence, where JSON's default
    keeps the last. Both continuity parsers always did, and a twin of the same
    shape must not read differently."""
    out: dict[Any, Any] = {}
    for key, value in pairs:
        out.setdefault(key, value)
    return out


def _loads(text: str) -> object:
    try:
        return json.loads(text, object_pairs_hook=_first_wins)
    except (ValueError, RecursionError):
        return None


def find_object(text: str) -> dict[str, Any] | None:
    """The reply's object: the whole text, else a leading fence's body, else
    the span from the first `{` to the last `}`. The first that decodes to an
    object wins, and `None` when none does. A repeated key, at any level, keeps
    its first value.

    `parse`'s first step, public because `parse` answers every item whatever
    it was sent: whether a reply held an object at all -- rather than one
    whose answers were unreadable -- is a question only this answers (the
    eval graders ask it)."""
    stripped = text.strip()
    candidates = [stripped]
    if stripped.startswith("```") and (fence := _FENCE.match(stripped)):
        candidates.append(fence.group(1).strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start:end + 1])
    for candidate in candidates:
        obj = _loads(candidate)
        if isinstance(obj, dict):
            return obj
    return None


def _flattened(obj: dict[str, Any], item: Item) -> tuple[object, object] | None:
    """(answers, rationale) for `item` answered with its question ids at the
    top of `obj` -- today's style -- or None when `obj` names none of
    them. The rationale is `rationale`, else a `LEGACY_RATIONALE_KEYS` key
    that is not one of the item's own question ids."""
    asked = {q.id for q in item.questions}
    if not asked & set(obj):
        return None
    keys = ("rationale", *(k for k in LEGACY_RATIONALE_KEYS if k not in asked))
    return obj, next((obj[k] for k in keys if k in obj), None)


def _foreign_index(obj: dict[str, Any], count: int) -> bool:
    """Whether `obj` has an index key (all digits; no question id is one) that
    is not one of the `count` items sent: `"2"` beside two items, or `"01"`.

    Such a reply was not keyed by our indices -- numbered from 1, most likely
    -- and its `"1"` may well be the model's answer to item 0. Renumbering it is
    a guess, so none of its keys is read as ours."""
    return any(key.isdigit() and not (key == str(int(key)) and int(key) < count)
               for key in obj)


def _item_object(obj: dict[str, Any], index: int,
                 items: Sequence[Item]) -> tuple[object, object] | None:
    """(answers, rationale) for item `index`, or None (no item) when the reply
    holds nothing that can be read as that item -- for every item, when the
    reply carries an index we did not send (`_foreign_index`), so each of them
    reads `NO_ITEM`: the object was there, but none of its keys is ours."""
    if _foreign_index(obj, len(items)):
        return None
    key = str(index)
    if key in obj:
        entry = obj[key]
        if not isinstance(entry, dict):
            return None
        if "answers" in entry:
            return entry["answers"], entry.get("rationale")
        # Keyed by its index with the `answers` level dropped: the flattened
        # shape under the key (a fallback without structured mode can send it).
        return _flattened(entry, items[index])
    if len(items) != 1 or "0" in obj:
        return None
    if "answers" in obj:  # unwrapped: the item's object, with no index key
        return obj["answers"], obj.get("rationale")
    return _flattened(obj, items[0])  # flattened: today's style


_UNREADABLE = Answer(None, "unreadable")
_NO_OBJECT = Answer(None, "unreadable", detail=NO_OBJECT)
_NO_ITEM = Answer(None, "unreadable", detail=NO_ITEM)


def was_read(answer: Answer) -> bool:
    """Whether the reply held and gave a value to this answer's question,
    however bad that value was: any answer, or an `unreadable` one with no
    detail or `NOT_AN_OPTION`. Not read: `NO_OBJECT`, `NO_ITEM`, `error`,
    `refused` and `abstained`.

    What it is for: an item the reply held and gave a value to may take a
    mapping's safe default (`uncertain`); an item the reply never reached may
    not, because storing a default would claim the model answered. A caller
    asks it of the question whose answer decides whether the item was answered
    (both continuity mappings ask it of `decision`, which never allows none)."""
    if answer.answer is not None:
        return True
    return answer.reason == "unreadable" and answer.detail in ("", NOT_AN_OPTION)


def _read_choice(q: Choice, value: object) -> Answer:
    if value is None:
        return Answer(None, "abstained") if q.allow_none else _UNREADABLE
    if isinstance(value, str):
        for opt in q.options:
            if value == opt.id:
                return Answer(opt.id)
        key = normalise(value)
        for opt in q.options:
            if key in {normalise(s) for s in (opt.id, *opt.aliases)}:
                return Answer(opt.id)
    return Answer(None, "unreadable", detail=NOT_AN_OPTION)


def _read(q: Question, answers: dict[str, Any]) -> Answer:
    if q.id not in answers:
        return _UNREADABLE
    value = answers[q.id]
    if isinstance(q, Predicate):
        return Answer(value) if isinstance(value, bool) else _UNREADABLE
    if isinstance(q, Choice):
        return _read_choice(q, value)
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(q.levels):
        return Answer(value)
    return _UNREADABLE


def parse(text: str, items: Sequence[Item], *, explain: bool) -> tuple[ItemResult, ...]:
    """Read a structured reply back into one `ItemResult` per item, in order.

    Never raises. Tolerates a fenced or prose-surrounded object, an item keyed
    by its index with its question ids directly under the key (the `answers`
    level dropped), and for a single item the unwrapped (`{"answers": ...}`)
    and flattened (question ids at the top level) shapes; a flattened shape,
    keyed or not, takes its rationale from `rationale`, else from a
    `LEGACY_RATIONALE_KEYS` key. Anything else, and
    any value of the wrong type, is `None` with reason `unreadable`; a reply
    with no object marks every answer `NO_OBJECT`, and an item the object does
    not hold marks its answers `NO_ITEM` -- every item of a reply that carries
    an index outside the items sent, whose keys are not ours to read (an answer
    left unread, never one misattributed). A repeated key keeps its first value.
    """
    obj = find_object(text) if isinstance(text, str) else None
    results = []
    for index, item in enumerate(items):
        if obj is None:
            results.append(ItemResult({q.id: _NO_OBJECT for q in item.questions}))
            continue
        found = _item_object(obj, index, items)
        answers, rationale = found if found is not None else (None, None)
        if not isinstance(answers, dict):
            results.append(ItemResult({q.id: _NO_ITEM for q in item.questions}))
            continue
        note = rationale.strip() if explain and isinstance(rationale, str) else ""
        results.append(ItemResult({q.id: _read(q, answers) for q in item.questions}, note))
    return tuple(results)


def unanswered(items: Sequence[Item], reason: str) -> tuple[ItemResult, ...]:
    """Every answer of every item `None` with `reason`."""
    return tuple(ItemResult({q.id: Answer(None, reason) for q in item.questions})
                 for item in items)


def chunks(items: Sequence[Item], size: int = MAX_ITEMS_PER_CALL,
           budget: int = MAX_ENUM_VALUES) -> list[tuple[int, tuple[Item, ...]]]:
    """`(offset, chunk)` pairs covering `items` in order, each at most `size`
    items and at most `budget` enum values (`enum_values`) -- except an item
    that alone exceeds `budget`, which is a chunk of its own (`validate`
    refuses one before `decide` chunks)."""
    if size < 1:
        raise ValueError("a chunk holds at least one item")
    out: list[tuple[int, tuple[Item, ...]]] = []
    held: list[Item] = []
    start = values = 0
    for index, item in enumerate(items):
        count = enum_values(item)
        if held and (len(held) == size or values + count > budget):
            out.append((start, tuple(held)))
            start, held, values = index, [], 0
        held.append(item)
        values += count
    if held:
        out.append((start, tuple(held)))
    return out
