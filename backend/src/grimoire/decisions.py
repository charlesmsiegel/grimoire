"""The decision contract: what `inference.decide` is asked, and what it answers.

A decision is a closed question about a piece of text -- is this scene over,
which of these characters speaks next, how far has this reply drifted from its
voice -- answered by a value drawn from a set the caller fixed in advance,
never by prose a call site has to pick apart. This module holds that contract
and everything about it that is pure: the request and result types, the
request's validation, the JSON Schema of one batch, the tolerant parser that
reads a reply back, and the chunking of a long batch -- and, for slice H's
native backends, the one mapping both adapters share: the reserved none an
`allow_none` choice adds (`native_choice_keys`) and how a wire key is read
back (`native_key`), what an endpoint cannot carry (`native_gap`), how a
provider's report becomes an `Answer` (`native_answer`) and a reply an
`ItemResult` (`native_result`),
the capture's record of a call (`outcome`) and the structured rendering of a
native result (`render`).

It is a gateway leaf on purpose, and imports nothing from the package but
the standard-library leaf `schemas` (spec §7.4, ruling 15; 01f-C3, whose
portable subset, budgets and reader it shares): the operation itself is `grimoire.inference`, and slice H's
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
- **The schema stays inside what both providers document** (`schemas.check`,
  which every batch schema passes). OpenAI's strict
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
import logging
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from . import schemas

log = logging.getLogger(__name__)

#: Why an answer is `None`. Closed: a backend that cannot say which of these
#: happened says `unreadable` rather than adding a fifth.
REASONS = ("unreadable", "refused", "abstained", "error")

#: How a decision was answered: through `generate(schema=)` and the parser
#: below, or by a provider's own decisions endpoint (slice H). These are also
#: the values a ledger row's `decision_mode` takes (`inference` stamps the
#: backend that served the call), so `store.usage` keys its native-row rule on
#: `NATIVE_BACKEND` rather than on a spelling of its own.
STRUCTURED_BACKEND = "structured"
NATIVE_BACKEND = "native"
BACKENDS = (STRUCTURED_BACKEND, NATIVE_BACKEND)

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

#: Grimoire's key for the reserved none of an `allow_none` choice, in a native
#: distribution and as a native `chosen` (slice H). Never an option id or
#: alias: `offerable` refuses it, so every builder drops it and `validate`
#: never sees it. No slug or ref can contain `<`, so no caller loses an option
#: to the reservation.
NONE_KEY = "<none>"

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
#: in it (a choice's options, a score's levels): strict mode's budget, kept
#: with the portable subset (`schemas.MAX_ENUM_VALUES`, which cites it) and
#: re-exported here. A batch is chunked under this as well as under
#: `MAX_ITEMS_PER_CALL`, and an item that alone exceeds it is refused.
MAX_ENUM_VALUES = schemas.MAX_ENUM_VALUES

#: Strict mode's string budgets (`schemas`, which cites them): per choice
#: (`validate`), and per schema, so `chunks` closes a chunk before it and
#: `validate` refuses an item that alone exceeds it. A batch schema has no
#: definitions and no consts, so its count is property names and enum strings
#: (`schema_chars`). Enforced whatever the backend, because the chain can fall
#: to a structured fallback (ruling 14).
MAX_ENUM_STRING_CHARS = schemas.MAX_ENUM_STRING_CHARS
ENUM_STRING_CHARS_ABOVE = schemas.ENUM_STRING_CHARS_ABOVE
MAX_SCHEMA_STRING_CHARS = schemas.MAX_SCHEMA_STRING_CHARS

#: Strict mode's object-property limit (`schemas`, which cites it).
#: `validate` bounds no item's question count, so an item of about 5,000
#: predicates reaches it; it is held like the string budget
#: (`schema_properties`, `validate`, `chunks`). The nesting limit
#: (`schemas.MAX_DEPTH`) cannot be reached: a batch schema is four objects
#: deep (batch, item, answers, a question's value) whatever it is asked.
MAX_SCHEMA_PROPERTIES = schemas.MAX_SCHEMA_PROPERTIES

#: Options one native `choice` may offer, OpenRouter's documented per-choice
#: limit (Task 3 lowers it if OpenAI documents a lower one: the lower governs
#: both, so the chain never depends on which provider serves). Counted with
#: the reserved none (`native_choice_keys`), which is why a nullable choice of
#: 255 options cannot go native (`native_gap`).
NATIVE_MAX_OPTIONS = 255

#: The reserved none's wire key (or the first free `none_2`, `none_3`, ...)
#: and the description it is offered with (ruling 23). Neither provider
#: documents an explicit none, so the adapters add this option.
NATIVE_NONE = "none"
NATIVE_NONE_TEXT = "None of the other options fits."

#: `native_answer`'s "the body named no answer", told apart from an explicit
#: `None` (which a nullable choice reads as abstained).
UNSTATED: Final = object()

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

    `stated` is what a native reply named when it named no option
    (`NOT_AN_OPTION`), as it named it, and ``""`` otherwise: a structured
    reply's raw text is still at hand to read it from, a native one's is not,
    and an eval grader tells an unoffered ``existing:<id>`` from an unknown
    word by it. Never an answer, and not compared: two answers that read
    alike are equal.
    """

    answer: bool | str | int | None
    reason: str = ""
    probability: float | None = None
    distribution: dict[str, float] | None = None
    detail: str = ""
    stated: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if (self.answer is None) != bool(self.reason):
            raise ValueError("an answer carries a reason exactly when it is None")
        if self.reason and self.reason not in REASONS:
            raise ValueError(f"unknown reason {self.reason!r}")
        if self.detail and self.reason != "unreadable":
            raise ValueError("a detail qualifies an unreadable answer only")
        if self.stated and self.detail != NOT_AN_OPTION:
            raise ValueError("a stated value qualifies an answer that named no option only")


@dataclass(frozen=True)
class ItemResult:
    """One item's answers, keyed by question id in question order, and its
    rationale (empty unless one was asked for and given).

    `backend` is the backend that answered this item (one of `BACKENDS`), the
    per-item truth when one batch is split across stages; `""` on an item
    nothing answered (`unanswered`) and on what `parse` returns, since the
    backend stamps it, not the parser."""

    answers: dict[str, Answer]
    rationale: str = ""
    backend: str = ""

    def __post_init__(self) -> None:
        if self.backend and self.backend not in BACKENDS:
            raise ValueError(f"unknown backend {self.backend!r}")


@dataclass(frozen=True)
class CallRecord:
    """One metered request a decide chain made: one structured call (a
    schema-refusal re-send is a record of its own) or one native item.

    `stage` is the call's index in the chain `run_stages` was handed, `mode`
    its backend (one of `BACKENDS`), and `items` the BATCH indices it carried.
    `row` is the ledger row its meter filed, or None when nothing was sent (a
    call refused unsent files no row). A failed call keeps its `LLMError`'s
    kind and HTTP status only, never its detail: the detail is the provider's
    own text, which no persisted store keeps. `hop` is "" for a call of the
    chain itself; it is reserved for an escalation hop, which sets
    "escalation".

    A row is what costs money, so this counts requests; nothing in production
    reads it (the eval runner does)."""

    stage: int
    mode: str
    items: tuple[int, ...]
    row: dict[str, Any] | None
    error_kind: str = ""
    error_status: int | None = None
    hop: str = ""


@dataclass(frozen=True)
class Decision:
    """What `decide` returns: one `ItemResult` per item, in input order, the
    backend that answered (one of `BACKENDS`, or `""` when stages with
    different backends answered items of one batch -- each `ItemResult`'s own
    `backend` is the per-item truth), what answered, and each call's usage
    row.

    What answered is `served`: every `(provider id, model)` that answered at
    least one call, each once, in the order it first answered, across every
    stage of the chain (`inference.stages`). A structured stage chunks its
    items (`chunks`), and each chunk is its own call down the attempt chain;
    a native stage makes one call per item. So one call may be answered by
    the primary and the next by its fallback. A call that failed answered
    nothing and names nothing.

    `provider` and `model` are that selection when it was the only one --
    every answering call was answered by the same route -- and both `""` when
    `served` names more than one: no single selection answered the decision,
    and naming the last call's (or the first's) would attribute the other
    calls' answers to a model that never saw them. `backend` follows the same
    rule over the stages' backends."""

    items: tuple[ItemResult, ...]
    backend: str
    provider: str = ""
    model: str = ""
    usage: tuple[dict[str, Any], ...] = ()
    served: tuple[tuple[str, str], ...] = ()
    #: Each failed unit's final error, in input order: what `decide` would
    #: have raised for that unit alone. A unit is a chunk on a structured
    #: stage and an item on a native stage -- the units of the last stage the
    #: items reached. Final means after the chunk's schema-refusal re-sends,
    #: and after every stage: an item that failed on one stage and answered
    #: on the next contributes nothing, and one that failed on every stage
    #: contributes the stages' failures composed as the facade composes any
    #: both-failed call (`llm.routes_failed`, each stage's own failure kept
    #: as `words`). An `llm_errors.LLMError`; typed loosely because this
    #: module imports nothing from the package. Empty when every item
    #: answered.
    errors: tuple[Exception, ...] = ()
    #: Every metered request the chain made (`CallRecord`), in the order the
    #: calls settled. Their rows are the rows `usage` holds, though not in
    #: its order (`usage` keeps a native stage's in item order); nothing in
    #: production reads this field.
    calls: tuple[CallRecord, ...] = ()

    def __post_init__(self) -> None:
        if self.backend and self.backend not in BACKENDS:
            raise ValueError(f"unknown backend {self.backend!r}")


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
    return bool(key) and key != NONE_KEY


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
        for spelling in (opt.id, *opt.aliases):
            if not offerable(spelling):
                raise DecideRequestError(
                    f"choice {q.id!r} cannot offer {spelling!r}: it is empty once "
                    f"normalised, or reserved")
            key = normalise(spelling)
            if key in seen:
                raise DecideRequestError(
                    f"choice {q.id!r}: {spelling!r} collides with {seen[key]!r} "
                    f"once normalised")
            seen[key] = spelling
    chars = sum(len(opt.id) for opt in q.options)
    if len(q.options) > ENUM_STRING_CHARS_ABOVE and chars > MAX_ENUM_STRING_CHARS:
        raise DecideRequestError(
            f"choice {q.id!r} offers {len(q.options)} options whose ids total {chars} "
            f"characters; an enum of more than {ENUM_STRING_CHARS_ABOVE} values "
            f"carries at most {MAX_ENUM_STRING_CHARS}")


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
    an option or level count out of bounds; an option id or alias `offerable`
    refuses; any two option ids or aliases that collide once normalised; a
    choice of more than `ENUM_STRING_CHARS_ABOVE` options whose ids total more
    than `MAX_ENUM_STRING_CHARS` characters; and an item whose own schema no
    chunk could carry -- more than `MAX_ENUM_VALUES` enum values, more than
    `MAX_SCHEMA_STRING_CHARS` characters (`schema_chars`) or more than
    `MAX_SCHEMA_PROPERTIES` properties (`schema_properties`). The schema limits
    are strict mode's, refused whatever the backend.
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
        properties, chars = schemas.tally(schema([item], explain=True))
        if chars > MAX_SCHEMA_STRING_CHARS:
            raise DecideRequestError(
                f"item {index}'s schema holds {chars} characters of names and enum "
                f"values; one call carries at most {MAX_SCHEMA_STRING_CHARS}")
        if properties > MAX_SCHEMA_PROPERTIES:
            raise DecideRequestError(
                f"item {index}'s schema holds {properties} properties; one call "
                f"carries at most {MAX_SCHEMA_PROPERTIES}")


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


# --- parse ------------------------------------------------------------------

def find_object(text: str) -> dict[str, Any] | None:
    """The reply's object: the whole text, else a leading fence's body, else
    the span from the first `{` to the last `}`. The first that decodes to an
    object wins, and `None` when none does. A repeated key, at any level, keeps
    its first value.

    `parse`'s first step, public because `parse` answers every item whatever
    it was sent: whether a reply held an object at all -- rather than one
    whose answers were unreadable -- is a question only this answers (the
    eval graders ask it). The portable reader restricted to objects
    (`schemas.find_value(arrays=False)`): a bare array is no object, and a
    list holding one object reads that object through the brace span."""
    found = schemas.find_value(text, arrays=False)
    return found if isinstance(found, dict) else None


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
    a guess, so none of its keys is read as ours.

    THE LIMIT. A reply numbered from 1 that answered every item carries `"n"`,
    one past the batch, and is caught; one that skipped an item may not be.
    `{"1": ..., "2": ...}` beside three items is in range, so it is read as
    items 1 and 2 -- and if the model meant items 0 and 1, a pair verdict
    (`duplicate` A->B, `related`) lands on the wrong candidate. Strict
    structured mode cannot send that shape (the schema requires `"0"` to
    `"n-1"`); a prompt-only re-send, a fallback without the mode, or a server
    that ignores `response_format` can. Identity's ``existing`` is safe from
    it (each row's decision offers ``existing:<id>`` for that row's
    candidates alone, so a shifted one reads `NOT_AN_OPTION`), and so are most
    of reconcile's status words (their evidence scene must be one of the
    item's own); the pair words are not.

    It is left unguarded because no test on the keys alone tells the two
    apart. The obvious one -- index keys with no `"0"` -- also refuses a
    reply keyed from 0 that answered every item but item 0, which reads
    exactly as a reply keyed from 1 that skipped the last one. That is how a
    reply answers only the items it has something to say about, and the
    continuity gate's replies take it often (tried, the rule turned most of
    the reconcile gate's decide reads into regressions), so refusing it would
    leave answers unread that nothing suggests were misnumbered.
    Telling them apart needs evidence the keys do not carry; a proposal
    stands behind review either way, so a misattributed one is a wrong
    suggestion the reviewer still has to accept, never a silent write."""
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


def held_no_object(answer: Answer | None) -> bool:
    """Whether `answer` says its reply held no JSON object at all (`NO_OBJECT`).

    The other side of `was_read`, for the same mappings: a batch none of whose
    deciding answers was read is undecodable only when every one of them held
    no object -- an object that holds no item is a reply that answered
    nothing, not one nobody could read. None (no such answer) is not."""
    return answer is not None and answer.detail == NO_OBJECT


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
           budget: int = MAX_ENUM_VALUES, chars: int = MAX_SCHEMA_STRING_CHARS,
           properties: int = MAX_SCHEMA_PROPERTIES) -> list[tuple[int, tuple[Item, ...]]]:
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


# --- native -----------------------------------------------------------------
#
# Slice H's native backends send one item per request to a provider's
# decisions endpoint. What both adapters share lives here, so the reserved
# none, the endpoint's limits and the reading of a report are one rule for
# both providers.

def native_choice_keys(q: Choice) -> tuple[tuple[str, str], ...]:
    """`(wire key, Grimoire key)` per option a native `choice` offers: each
    option as itself, in order, and with `allow_none` one more, the reserved
    none -- `NATIVE_NONE`, or the first of `none_2`, `none_3`, ... that no
    option id equals -- mapped to `NONE_KEY`. A one-option nullable choice is
    therefore a two-option native choice."""
    keys = tuple((opt.id, opt.id) for opt in q.options)
    if not q.allow_none:
        return keys
    taken = {opt.id for opt in q.options}
    wire, n = NATIVE_NONE, 1
    while wire in taken:
        n += 1
        wire = f"{NATIVE_NONE}_{n}"
    return (*keys, (wire, NONE_KEY))


#: What `native_key` hands over for an unoffered wire key spelled `NONE_KEY`.
_UNOFFERED: Final = object()


def native_key(keys: Mapping[str, str], wire: object) -> object:
    """A choice's wire key read back as Grimoire's, through `keys` (a
    `dict(native_choice_keys(q))`): an offered key as its option id (the
    reserved none as `NONE_KEY`), anything else as itself, so `native_answer`
    reads it as `not_an_option` and a distribution keyed by it as invalid.
    The one exception is an unoffered key spelled `NONE_KEY`, which would read
    as the reserved none: it goes as a value no option id and no distribution
    key can equal."""
    if isinstance(wire, str) and wire in keys:
        return keys[wire]
    return _UNOFFERED if wire == NONE_KEY else wire


def native_result(item: Item, answers: Mapping[str, Answer], provider: str) -> ItemResult | None:
    """`item`'s result from one decisions reply: `answers` holds an `Answer`
    for each of its questions the reply answered, keyed by question id, and a
    question it left out is `unreadable` (one warning per call, naming
    `provider`). None when the reply answered none of them: that is not an
    answer but a failed call (ruling 4, I2), which the adapter raises as its
    own `bad_response` so the item falls through to the next stage."""
    missing = [q.id for q in item.questions if q.id not in answers]
    if len(missing) == len(item.questions):
        return None
    if missing:
        log.warning("%s's decisions reply left %d of %d questions unanswered",
                    provider, len(missing), len(item.questions))
    return ItemResult({q.id: answers.get(q.id, Answer(None, "unreadable"))
                       for q in item.questions},
                      rationale="", backend=NATIVE_BACKEND)


def native_gap(item: Item) -> str:
    """`""` when a decisions endpoint can carry `item`; otherwise one sentence
    naming what it cannot (ruling 25), which the native call refuses unsent.
    Today that is a choice whose options, with the reserved none, pass
    `NATIVE_MAX_OPTIONS`. A limit is named here, never met by truncating."""
    for q in item.questions:
        if isinstance(q, Choice) and (n := len(native_choice_keys(q))) > NATIVE_MAX_OPTIONS:
            return (f"Question {q.id} offers {n} options including none; "
                    f"a decisions endpoint takes at most {NATIVE_MAX_OPTIONS}.")
    return ""


def _probability(value: object) -> float | None:
    """`value` as a probability: a finite number (never a bool) in [0, 1].
    The range is checked before anything converts it, so an int too large for
    a float (`10**400`) is out of range rather than an `OverflowError`; NaN
    fails both comparisons."""
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 <= value <= 1 and math.isfinite(value)):
        return float(value)
    return None


def _legal_keys(q: Question) -> set[str]:
    if isinstance(q, Choice):
        return {opt.id for opt in q.options} | ({NONE_KEY} if q.allow_none else set())
    if isinstance(q, Score):
        return {str(i) for i in range(len(q.levels))}
    return set()


def _distribution(q: Question, value: object) -> dict[str, float] | None:
    """`value` as `q`'s distribution, or None: a non-empty mapping whose every
    key is legal for `q` and whose every value is a probability. An invalid
    report is dropped whole, never repaired."""
    if not isinstance(value, Mapping) or not value:
        return None
    legal = _legal_keys(q)
    out: dict[str, float] = {}
    for key, weight in value.items():
        p = _probability(weight)
        if not isinstance(key, str) or key not in legal or p is None:
            return None
        out[key] = p
    return out


def _argmax(distribution: dict[str, float]) -> str | None:
    """The distribution's one most probable key; None on a tie at the top."""
    top = max(distribution.values())
    winners = [key for key, weight in distribution.items() if weight == top]
    return winners[0] if len(winners) == 1 else None


def native_answer(q: Question, *, chosen: object = UNSTATED, probability: object = None,
                  distribution: object = None, refused: bool = False) -> Answer:
    """A native endpoint's report for `q` as an `Answer`.

    The adapter hands over keys already mapped back to Grimoire's (option ids
    or `NONE_KEY`, level indices as `str(i)`), and never a provider's
    probability-weighted `score` as `chosen` (ruling 24). `refused` is
    `refused`. Otherwise `probability` is kept only when it is a probability
    and `distribution` only when it is valid for `q` (`_distribution`), and
    both ride on the answer, an answer of None included.

    - A predicate: a `bool` `chosen` is the answer, whatever the probability
      beside it says (M11); else P(true) above 0.5 is True, below is False,
      exactly 0.5 is `abstained`; with no usable probability, `unreadable`.
    - A choice: `chosen` an exact option id is that option (aliases are the
      structured parser's, §7.4); `NONE_KEY` or None is `abstained` with
      `allow_none` and `unreadable` without; any other value is `unreadable`
      with `NOT_AN_OPTION`, a string kept as its `stated`. With nothing chosen, the distribution's argmax --
      `abstained` on a tie or on `NONE_KEY` -- and with no usable
      distribution, `unreadable`.
    - A score: `chosen` an `int` index (never a bool or a float); else the
      distribution's argmax under the same tie rule; else `unreadable`.
    """
    if refused:
        return Answer(None, "refused")
    p = _probability(probability)
    dist = _distribution(q, distribution)
    if isinstance(q, Predicate):
        value, reason, detail = _native_predicate(chosen, p)
    elif isinstance(q, Choice):
        value, reason, detail = _native_choice(q, chosen, dist)
    else:
        value, reason, detail = _native_score(q, chosen, dist)
    stated = chosen if detail == NOT_AN_OPTION and isinstance(chosen, str) else ""
    return Answer(value, reason, probability=p, distribution=dist, detail=detail,
                  stated=stated)


#: `(answer, reason, detail)`: an `Answer` before its report rides on it.
_Read = tuple[bool | str | int | None, str, str]


def _native_predicate(chosen: object, p: float | None) -> _Read:
    if isinstance(chosen, bool):
        return chosen, "", ""
    if p is None:
        return None, "unreadable", ""
    if p == 0.5:
        return None, "abstained", ""
    return p > 0.5, "", ""


def _from_distribution(dist: dict[str, float] | None) -> tuple[str | None, str]:
    """`(key, "")` for the distribution's argmax; `(None, reason)` when there
    is no usable distribution (`unreadable`), or its top is a tie or the
    reserved none (`abstained`)."""
    if dist is None:
        return None, "unreadable"
    top = _argmax(dist)
    return (None, "abstained") if top in (None, NONE_KEY) else (top, "")


def _native_choice(q: Choice, chosen: object, dist: dict[str, float] | None) -> _Read:
    if chosen is UNSTATED:
        key, reason = _from_distribution(dist)
        return key, reason, ""
    if isinstance(chosen, str) and chosen in {opt.id for opt in q.options}:
        return chosen, "", ""
    if chosen is None or chosen == NONE_KEY:
        return None, "abstained" if q.allow_none else "unreadable", ""
    return None, "unreadable", NOT_AN_OPTION


def _native_score(q: Score, chosen: object, dist: dict[str, float] | None) -> _Read:
    if isinstance(chosen, int) and not isinstance(chosen, bool) and 0 <= chosen < len(q.levels):
        return chosen, "", ""
    key, reason = _from_distribution(dist)
    return (None if key is None else int(key)), reason, ""


#: How far apart two summed masses may be and still tie in `regrouped`: a sum
#: of reported probabilities carries float error (0.1 + 0.2 is not 0.3), and
#: an error must not decide which of two equal meanings wins.
MASS_TIE = 1e-9


def regrouped(answer: Answer, group: Callable[[str], str],
              order: Sequence[str]) -> str | None:
    """The group a choice's reported distribution puts the most mass on, when
    that is not the group of the option `answer` chose; None when it is (a
    tie at the top that includes it counts as it), and when there is no
    chosen option or no distribution -- a structured answer, which carries
    none, is read as chosen.

    For a choice that spells one meaning several ways (continuity's folded
    options: ``duplicate_a_into_b`` and ``duplicate_b_into_a``, or one
    ``existing:<id>`` per candidate), where an endpoint that scores options
    one by one splits that meaning's probability across its spellings: 0.3 on
    each of two and 0.4 on a rival is a 0.6 verdict that the per-option
    argmax, and an endpoint's own `choice`, both lose. `group` maps an option
    id to the meaning it spells; the reserved none is no group. A tie at the
    top that leaves the chosen group out goes to the group first in `order`
    (the caller's option order), then to the first reported."""
    if not isinstance(answer.answer, str) or not answer.distribution:
        return None
    mass: dict[str, float] = {}
    for key, weight in answer.distribution.items():
        if key != NONE_KEY:
            mass[group(key)] = mass.get(group(key), 0.0) + weight
    if not mass:
        return None
    top = max(mass.values())
    winners = [g for g, weight in mass.items() if weight >= top - MASS_TIE]
    if group(answer.answer) in winners:
        return None
    rank = {g: n for n, g in enumerate(order)}
    return min(winners, key=lambda g: rank.get(g, len(rank)))


def _present(record: dict[str, Any]) -> dict[str, Any]:
    """`record` without its keys whose value is empty or None."""
    return {key: value for key, value in record.items()
            if value is not None and value != "" and value != {} and value != []}


def outcome(mode: str, provider: str, model: str,
            results: Sequence[ItemResult] | None = None, error: str = "") -> dict[str, Any]:
    """The capture's record of one call (spec §9.4): its mode and what served
    it, then each item's backend, normalised answers -- `answer`, always
    present (None as null), with its `reason`, `detail`, `probability` and
    `distribution` -- and rationale; or, on a failure, the `error`. A key
    whose value is empty or None is left out (an answer's own `answer`
    excepted)."""
    head = _present({"mode": mode, "provider": provider, "model": model})
    if error:
        return {**head, "error": error}
    items = []
    for result in results or ():
        answers = {
            qid: {"answer": a.answer, **_present({
                "reason": a.reason, "detail": a.detail, "probability": a.probability,
                "distribution": dict(a.distribution) if a.distribution else None})}
            for qid, a in result.answers.items()}
        items.append(_present({"backend": result.backend, "answers": answers,
                               "rationale": result.rationale}))
    return {**head, "items": items}


def render(results: Sequence[ItemResult], items: Sequence[Item], *, explain: bool) -> str:
    """`results` written as the structured backend's reply to `items` --
    `{"0": {"answers": {qid: value}, "rationale": ...}}`, a None answer as
    JSON null and `rationale` only when `explain` -- so the structured
    graders can read a native `Decision` (`evals/run.py --live`)."""
    out: dict[str, Any] = {}
    for index, (result, item) in enumerate(zip(results, items, strict=True)):
        entry: dict[str, Any] = {"answers": {
            q.id: (result.answers[q.id].answer if q.id in result.answers else None)
            for q in item.questions}}
        if explain:
            entry["rationale"] = result.rationale
        out[str(index)] = entry
    return json.dumps(out)
