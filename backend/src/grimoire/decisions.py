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
kind no endpoint has is lowered to kinds they do (`native_form`) and read
back (`native_lift`), how a provider's report becomes an `Answer`
(`native_answer`) and a reply an `ItemResult` (`native_result`),
the capture's record of a call (`outcome`) and the structured rendering of a
native result (`render`).

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
  integer, null, array), `enum`, `anyOf`, `items`, `required`, `properties`
  and `additionalProperties: false`. Anthropic refuses numeric bounds, so a
  score is an integer `enum`; a nullable choice is `anyOf` with a null
  branch, never a type array; and a ranking or a selection is an `array` of
  an `enum` with no `minItems`, `maxItems` or `uniqueItems` (Anthropic takes
  a `minItems` of only 0 or 1, OpenAI documents no `uniqueItems`), so the
  parser holds its count and its uniqueness.
- **The parser never raises.** It answers every item it was handed, so one
  unreadable reply costs the answers it held and nothing more.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar, Final

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

#: Candidates one `Rank` may order (01e-C1). Argued from structure alone: the
#: structured reply echoes each candidate id, so it grows with the count; a
#: long permutation is where duplicates and omissions appear; and the native
#: lowering puts one question per candidate into a single request, where
#: neither endpoint documents a questions-per-request bound. A caller reranks
#: a pool it has already narrowed. To be tuned against real prompts later
#: (01a's reports), never against a measured library.
MIN_RANK_CANDIDATES, MAX_RANK_CANDIDATES = 2, 32

#: Options one `MultiSelect` may offer (01e-C3a), argued as for
#: `MAX_RANK_CANDIDATES`: a native selection is one predicate per option in
#: one request. To be tuned later, the same way.
MIN_SELECT_OPTIONS, MAX_SELECT_OPTIONS = 1, 32

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

#: Strict mode's string budgets, from the same page, checked 2026-10-08: "For a
#: single enum property with string values, the total string length of all
#: enum values cannot exceed 15,000 characters when there are more than 250
#: enum values", and "the total string length of all property names,
#: definition names, enum values, and const values cannot exceed 120,000
#: characters". The first is per choice (`validate`); the second is per schema,
#: so `chunks` closes a chunk before it and `validate` refuses an item that
#: alone exceeds it. A batch schema has no definitions and no consts, so its
#: count is property names and enum strings (`schema_chars`). Enforced
#: whatever the backend, because the chain can fall to a structured fallback
#: (ruling 14).
MAX_ENUM_STRING_CHARS = 15_000
ENUM_STRING_CHARS_ABOVE = 250
MAX_SCHEMA_STRING_CHARS = 120_000

#: Strict mode's other schema limit a legal request can reach (same page,
#: checked 2026-10-08): "A schema may have up to 5000 object properties total,
#: with up to 10 levels of nesting." `validate` bounds no item's question
#: count, so an item of about 5,000 predicates reaches the first; it is held
#: like the string budget (`schema_properties`, `validate`, `chunks`). The
#: nesting limit cannot be reached: a batch schema is five levels deep at most
#: (batch, item, answers, a ranking's array and the string it holds)
#: whatever it is asked.
MAX_SCHEMA_PROPERTIES = 5000

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

#: The predicate a native selection asks of each option (01e-C3a). A fixed
#: wording is honest here, as it is not for a rank: "is this option selected"
#: is exactly the per-option question a selection asks.
NATIVE_SELECT_TEXT = ("{instructions}\n\nOption {id}: {description}\n\n"
                      "Is this option one of those selected?")

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

    #: What `decide/user.j2` and `decide/system.j2` branch on: a class
    #: constant, never a constructor or equality field.
    KIND: ClassVar[str] = "predicate"

    id: str
    instructions: str


@dataclass(frozen=True)
class Choice:
    """Pick one option; answered with its `id`. With `allow_none`, an explicit
    null is a legitimate answer (`None`, reason `abstained`)."""

    KIND: ClassVar[str] = "choice"

    id: str
    instructions: str
    options: tuple[Option, ...]
    allow_none: bool = False


@dataclass(frozen=True)
class Score:
    """Place the text on an ordered scale; answered with a level's index."""

    KIND: ClassVar[str] = "score"

    id: str
    instructions: str
    levels: tuple[str, ...]


@dataclass(frozen=True)
class Rank:
    """Order `candidates`, best first (01e-C1); answered with a `Ranking`.

    `top` asks for at least that many to be ranked (None: all of them); the
    rest come back unranked. With `allow_none`, an explicit null -- "these
    cannot be ordered" -- is `None`, reason `abstained`, on the structured
    path only: no decisions endpoint can say it.

    `pointwise` is the yes/no question a native endpoint asks of ONE
    candidate at a time, which is how a rank is lowered there; it has no
    default wording, because only the caller knows what the per-candidate
    question is. A rank that may meet a native Decision model must set it,
    or every native attempt is refused unsent (`native_gap`)."""

    KIND: ClassVar[str] = "rank"

    id: str
    instructions: str
    candidates: tuple[Option, ...]
    top: int | None = None
    allow_none: bool = False
    pointwise: str = ""


@dataclass(frozen=True)
class MultiSelect:
    """Pick any subset of `options` (01e-C3a); answered with a tuple of
    option ids in the CALLER'S option order -- a set has no order the model
    can be trusted to mean, and a consumer that fans out over the selection
    must do so deterministically (a caller that wants the model's order asks
    a `Rank`).

    `()` -- none of these -- is a real answer. `None`, reason `abstained`, is
    "cannot say", which only `allow_none` permits; a consumer must never read
    one as the other. The selection holds `min` to `max` options, `max` None
    meaning all of them (and 0 meaning none, not "all")."""

    KIND: ClassVar[str] = "select"

    id: str
    instructions: str
    options: tuple[Option, ...]
    min: int = 0
    max: int | None = None
    allow_none: bool = False

    @property
    def most(self) -> int:
        """The selection's upper bound: `max`, or every option when None."""
        return len(self.options) if self.max is None else self.max


#: What joins an action to its target in a `Joint`'s flattened key. ASCII,
#: and holds no space or hyphen that `normalise` would rewrite, so a key
#: survives normalisation as itself.
JOINT_SEP = "=>"


def joint_key(head: str, tail: str | None) -> str:
    """A legal pair's one spelling: `head` for a head that takes no tail,
    else `head + JOINT_SEP + tail`. The key a pair has everywhere a key is
    needed -- a distribution, a draw and its replay record, an answer filter,
    a capture, an eval render."""
    return head if tail is None else f"{head}{JOINT_SEP}{tail}"


@dataclass(frozen=True)
class Pair:
    """A `Joint`'s answer: the head (an action) and its tail (the target),
    None for a head that takes none. `key` is its one spelling, so a pair
    and its key cannot disagree."""

    head: str
    tail: str | None

    @property
    def key(self) -> str:
        return joint_key(self.head, self.tail)


def split_joint(key: str) -> Pair:
    """`joint_key`'s inverse; `ValueError` on a key with no head, an empty
    tail, or more than one separator (no legal pair is spelled so: `validate`
    refuses `JOINT_SEP` in any id)."""
    head, sep, tail = key.partition(JOINT_SEP)
    if not head or (sep and (not tail or JOINT_SEP in tail)):
        raise ValueError(f"{key!r} spells no pair")
    return Pair(head, tail if sep else None)


@dataclass(frozen=True)
class Joint:
    """One action and one target conditioned on it (01e-C3b); answered with
    a `Pair`.

    `tails` maps a head id to its legal targets; a head missing from it, or
    mapped to `()`, takes no tail. The conditioning IS the legal-pair set: the
    caller lists only pairs that are legal, and the model never sees another.
    On both paths it is one flattened `Choice` (`choice`) whose options are
    those pairs, keyed by `joint_key` -- never two questions, since a native
    endpoint answers each question of an item without seeing the others, so
    a second "target" question would be answered blind of the first. The
    native answer's distribution is therefore a joint distribution over
    legal pairs. A set of targets, or more legal pairs than a choice offers,
    is two `decide` calls: the action, then its targets."""

    KIND: ClassVar[str] = "joint"

    id: str
    instructions: str
    heads: tuple[Option, ...]
    tails: tuple[tuple[str, tuple[Option, ...]], ...] = ()
    allow_none: bool = False

    @property
    def choice(self) -> Choice:
        """The flattened choice: every legal pair in head order, then tail
        order, described `head -> tail`."""
        targets = dict(self.tails)
        options = tuple(
            Option(head.id, head.description) if tail is None
            else Option(joint_key(head.id, tail.id), f"{head.description} -> {tail.description}")
            for head in self.heads for tail in targets.get(head.id, ()) or (None,))
        return Choice(self.id, self.instructions, options, self.allow_none)


Question = Predicate | Choice | Score | Rank | MultiSelect | Joint


@dataclass(frozen=True)
class Ranking:
    """A `Rank`'s answer: `tiers`, best first, whose members are tied, and
    `rest`, the candidates the answer did not rank, in input order.

    A value object rather than nested tuples on purpose: a ranking with ties
    and a flat order are different facts, and flattening one by input order
    without noticing is the silent tie-break 01e-C2 exists to prevent.
    `flat` is the only flattening, and it makes the caller choose."""

    tiers: tuple[tuple[str, ...], ...]
    rest: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for group in (*self.tiers, self.rest):
            for candidate in group:
                if candidate in seen:
                    raise ValueError(f"a ranking names {candidate!r} twice")
                seen.add(candidate)
        if any(not tier for tier in self.tiers):
            raise ValueError("a ranking's tier names at least one candidate")

    def flat(self, tiebreak: Callable[[str], Any] | None = None, *,
             rest: bool = False) -> tuple[str, ...]:
        """The tiers in order, each tier's members ordered by `tiebreak` (a
        sort key). With no tie-break, a tier of more than one raises
        `ValueError`: who goes first was never said. `rest`, the candidates
        nobody ranked, is left out unless asked for, and then follows as one
        bottom group under the same rule."""
        groups = (*self.tiers, self.rest) if rest and self.rest else self.tiers
        out: list[str] = []
        for group in groups:
            if len(group) > 1 and tiebreak is None:
                raise ValueError(
                    f"{len(group)} candidates are tied ({', '.join(group)}); "
                    f"flattening them needs a tie-break")
            out.extend(sorted(group, key=tiebreak) if len(group) > 1 else group)
        return tuple(out)

    def position(self, candidate: str) -> int | None:
        """The index of `candidate`'s tier, best 0; None for one in `rest`
        or not in the ranking at all."""
        for index, tier in enumerate(self.tiers):
            if candidate in tier:
                return index
        return None


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

    `marginals` (01e-C4) are per-key probabilities a native endpoint reported
    independently -- each candidate's or option's P(true) under its lowered
    predicate -- which do not sum to 1. They are never a `distribution`,
    which is categorical and is what a sampler draws from, and are never
    sampled. `expected` (01e-C2) is a native score's probability-weighted
    level over its reported distribution (`native_answer`). Both ride on the
    answer as the other reports do, an answer of None included, and both are
    None on every structured answer.
    """

    answer: bool | str | int | Ranking | Pair | tuple[str, ...] | None
    reason: str = ""
    probability: float | None = None
    distribution: dict[str, float] | None = None
    detail: str = ""
    marginals: dict[str, float] | None = None
    expected: float | None = None
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
        if self.marginals is not None and not (isinstance(self.marginals, Mapping) and all(
                isinstance(key, str) and _probability(p) is not None
                for key, p in self.marginals.items())):
            raise ValueError("marginals are probabilities keyed by string")
        if self.expected is not None and not _finite(self.expected):
            raise ValueError("an expected level is a finite number")


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


def _check_options(kind: str, qid: str, options: Sequence[Option], low: int,
                   high: int, noun: str = "options") -> None:
    """The option rules a choice and every type built like one share: a
    count in `low..high`, each an `Option`, every id and alias `offerable`,
    none colliding once normalised, and strict mode's enum string budget.
    `kind` and `noun` only word the refusal."""
    if not low <= len(options) <= high:
        raise DecideRequestError(
            f"{kind} {qid!r} offers {len(options)} {noun}; it needs {low}-{high}")
    seen: dict[str, str] = {}
    for opt in options:
        if not isinstance(opt, Option):
            raise DecideRequestError(f"{kind} {qid!r} has an option that is not an Option")
        for spelling in (opt.id, *opt.aliases):
            if not offerable(spelling):
                raise DecideRequestError(
                    f"{kind} {qid!r} cannot offer {spelling!r}: it is empty once "
                    f"normalised, or reserved")
            key = normalise(spelling)
            if key in seen:
                raise DecideRequestError(
                    f"{kind} {qid!r}: {spelling!r} collides with {seen[key]!r} "
                    f"once normalised")
            seen[key] = spelling
    chars = sum(len(opt.id) for opt in options)
    if len(options) > ENUM_STRING_CHARS_ABOVE and chars > MAX_ENUM_STRING_CHARS:
        raise DecideRequestError(
            f"{kind} {qid!r} offers {len(options)} {noun} whose ids total {chars} "
            f"characters; an enum of more than {ENUM_STRING_CHARS_ABOVE} values "
            f"carries at most {MAX_ENUM_STRING_CHARS}")


def _check_choice(q: Choice) -> None:
    low = MIN_OPTIONS_WITH_NONE if q.allow_none else MIN_OPTIONS
    _check_options("choice", q.id, q.options, low, MAX_OPTIONS)


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_select(q: MultiSelect) -> None:
    _check_options("selection", q.id, q.options, MIN_SELECT_OPTIONS, MAX_SELECT_OPTIONS)
    if not (_count(q.min) and (q.max is None or _count(q.max))
            and 0 <= q.min <= q.most <= len(q.options)):
        raise DecideRequestError(
            f"selection {q.id!r} selects {q.min!r} to {q.max!r} of {len(q.options)} "
            f"options; it needs 0 <= min <= max <= {len(q.options)}")


def _check_joint(q: Joint) -> None:
    """A joint's own rules -- every `tails` key a head and no head twice, no
    `JOINT_SEP` in any id, no aliases (pairing them would multiply them) --
    then the flattened choice's, under the choice's bounds."""
    _check_tails(q)
    for opt in (*q.heads, *(t for _, targets in q.tails for t in targets)):
        if not isinstance(opt, Option):
            raise DecideRequestError(f"joint {q.id!r} has a head that is not an Option")
        if JOINT_SEP in opt.id:
            raise DecideRequestError(f"joint {q.id!r} cannot offer {opt.id!r}: it holds "
                                     f"{JOINT_SEP!r}, which joins a pair's key")
        if opt.aliases:
            raise DecideRequestError(f"joint {q.id!r}: {opt.id!r} has aliases, which a "
                                     f"joint does not take")
        if not offerable(opt.id):
            raise DecideRequestError(f"joint {q.id!r} cannot offer {opt.id!r}: it is "
                                     f"empty once normalised, or reserved")
    low = MIN_OPTIONS_WITH_NONE if q.allow_none else MIN_OPTIONS
    _check_options("joint", q.id, q.choice.options, low, MAX_OPTIONS, "legal pairs")


def _check_tails(q: Joint) -> None:
    heads = {opt.id for opt in q.heads if isinstance(opt, Option)}
    named: set[str] = set()
    for entry in q.tails:
        if not (isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[1], tuple)):
            raise DecideRequestError(f"joint {q.id!r} has a tails entry that is not "
                                     f"(head id, targets)")
        head, targets = entry
        if not isinstance(head, str) or head not in heads:
            raise DecideRequestError(f"joint {q.id!r} lists targets for {head!r}, "
                                     f"which is not one of its heads")
        if head in named:
            raise DecideRequestError(f"joint {q.id!r} lists targets for {head!r} twice")
        named.add(head)
        if any(not isinstance(tail, Option) for tail in targets):
            raise DecideRequestError(f"joint {q.id!r} has a target that is not an Option")


def _check_rank(q: Rank) -> None:
    _check_options("rank", q.id, q.candidates, MIN_RANK_CANDIDATES, MAX_RANK_CANDIDATES,
                   "candidates")
    if q.top is not None and (isinstance(q.top, bool) or not isinstance(q.top, int)
                              or not 1 <= q.top <= len(q.candidates)):
        raise DecideRequestError(
            f"rank {q.id!r} asks for the top {q.top!r} of {len(q.candidates)} candidates; "
            f"it needs 1-{len(q.candidates)}")


def _check_question(q: object, index: int) -> None:
    if not isinstance(q, (Predicate, Choice, Score, Rank, MultiSelect, Joint)):
        raise DecideRequestError(f"item {index} has a question of unknown type")
    if not q.id.strip():
        raise DecideRequestError(f"item {index} has a question with an empty id")
    if q.id.isdigit() or q.id in RESERVED_IDS:
        raise DecideRequestError(f"question id {q.id!r} is reserved")
    if isinstance(q, Choice):
        _check_choice(q)
    elif isinstance(q, Rank):
        _check_rank(q)
    elif isinstance(q, MultiSelect):
        _check_select(q)
    elif isinstance(q, Joint):
        _check_joint(q)
    elif isinstance(q, Score) and not MIN_LEVELS <= len(q.levels) <= MAX_LEVELS:
        raise DecideRequestError(
            f"score {q.id!r} has {len(q.levels)} levels; "
            f"it needs {MIN_LEVELS}-{MAX_LEVELS}")


def _enum_count(q: Question) -> int:
    if isinstance(q, Choice):
        return len(q.options)
    if isinstance(q, Score):
        return len(q.levels)
    if isinstance(q, Rank):
        return len(q.candidates)
    if isinstance(q, MultiSelect):
        return len(q.options)
    if isinstance(q, Joint):
        return len(q.choice.options)
    return 0


def enum_values(item: Item) -> int:
    """How many enum values `item` adds to a batch schema: each choice's
    options, each score's levels, each rank's candidates, each selection's
    options and each joint's legal pairs (a predicate is a
    plain boolean, and a nullable question's null branch is a type, not a
    value)."""
    return sum(_enum_count(q) for q in item.questions)


def kinds(items: Sequence[Item]) -> frozenset[str]:
    """Every question `KIND` `items` ask: what `decide/system.j2` renders a
    bullet for, so a batch is told only how to answer what it holds."""
    return frozenset(q.KIND for item in items for q in item.questions)


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
        properties, chars = _tally(schema([item], explain=True))
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
    if isinstance(q, Joint):
        # A flat string enum of the legal pairs' keys: nested anyOf-of-objects
        # per head would need single-value enums standing in for `const` and
        # multiply the properties, and buy nothing the flat enum does not.
        return _question_schema(q.choice)
    if isinstance(q, Predicate):
        return {"type": "boolean"}
    if isinstance(q, Choice):
        ids = {"type": "string", "enum": [opt.id for opt in q.options]}
        return {"anyOf": [ids, {"type": "null"}]} if q.allow_none else ids
    if isinstance(q, (Rank, MultiSelect)):
        # No minItems, maxItems or uniqueItems (the module docstring): the
        # parser enforces the count and the uniqueness.
        offered = q.candidates if isinstance(q, Rank) else q.options
        listed = {"type": "array",
                  "items": {"type": "string", "enum": [opt.id for opt in offered]}}
        return {"anyOf": [listed, {"type": "null"}]} if q.allow_none else listed
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


def _tally(node: object) -> tuple[int, int]:
    """(properties, characters) of a schema as strict mode counts them: every
    entry of every `properties` mapping, and the characters of its key and of
    every string `enum` value. A key under `properties` is a name, never a
    keyword, so a question called `enum` is counted as a property."""
    properties = chars = 0
    below: list[object] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                properties += len(value)
                chars += sum(len(name) for name in value)
                below.extend(value.values())
            elif key == "enum" and isinstance(value, list):
                chars += sum(len(v) for v in value if isinstance(v, str))
            else:
                below.append(value)
    elif isinstance(node, list):
        below.extend(node)
    for sub in below:
        p, c = _tally(sub)
        properties, chars = properties + p, chars + c
    return properties, chars


def schema_chars(items: Sequence[Item]) -> int:
    """The characters strict mode's 120,000 budget counts in `items`' batch
    schema: every property name and every string enum value. Counted with
    `explain=True`, the worst case, so `rationale` counts whether asked or
    not; integer enums (a score's levels) count nothing."""
    return _tally(schema(items, explain=True))[1]


def schema_properties(items: Sequence[Item]) -> int:
    """The object properties strict mode's 5,000 limit counts in `items`' batch
    schema, with `explain=True`: each item's index, its `answers` and
    `rationale`, and one per question."""
    return _tally(schema(items, explain=True))[0]


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
    found = _match(q.options, value)
    return Answer(None, "unreadable", detail=NOT_AN_OPTION) if found is None else Answer(found)


def _match(options: Sequence[Option], value: object) -> str | None:
    """The option `value` names: exactly first, then through `normalise` and
    aliases -- a choice's rule, shared by every list-of-ids reading."""
    if not isinstance(value, str):
        return None
    for opt in options:
        if value == opt.id:
            return opt.id
    key = normalise(value)
    for opt in options:
        if key in {normalise(s) for s in (opt.id, *opt.aliases)}:
            return opt.id
    return None


def _read_ids(options: Sequence[Option], value: object) -> tuple[str, ...] | Answer:
    """A list of option ids read in the order given, or the unreadable
    `Answer` it is: not a list, an entry naming no option (`NOT_AN_OPTION`,
    with no `stated`, as a structured choice sets none) or an entry naming
    one already listed (no detail: a value was given, and which position was
    meant cannot be told). An unknown entry is never dropped: dropping it
    would move every later entry up a place, and an answer left unread is
    better than one misread."""
    if not isinstance(value, list):
        return _UNREADABLE
    named = [_match(options, entry) for entry in value]
    if any(n is None for n in named):
        return Answer(None, "unreadable", detail=NOT_AN_OPTION)
    ids = tuple(n for n in named if n is not None)
    if len(set(ids)) != len(ids):
        return _UNREADABLE
    return ids


def _read_rank(q: Rank, value: object) -> Answer:
    """A structured ranking: singleton tiers in the order listed, and `rest`
    the candidates left unlisted, in input order. A null is `abstained` only
    with `allow_none`; fewer distinct candidates than `top` (all of them when
    None) is `unreadable` -- read, and incomplete."""
    if value is None:
        return Answer(None, "abstained") if q.allow_none else _UNREADABLE
    ids = _read_ids(q.candidates, value)
    if isinstance(ids, Answer):
        return ids
    if len(ids) < (len(q.candidates) if q.top is None else q.top):
        return _UNREADABLE
    listed = set(ids)
    return Answer(Ranking(tiers=tuple((c,) for c in ids),
                          rest=tuple(opt.id for opt in q.candidates if opt.id not in listed)))


def _joint_answer(answer: Answer) -> Answer:
    """The flattened choice's answer as a joint's: its option id split back
    into the `Pair` it keys, every report riding as it was (a distribution
    stays keyed by the flattened key, the one spelling of a pair)."""
    if not isinstance(answer.answer, str):
        return answer
    return replace(answer, answer=split_joint(answer.answer))


def _read_select(q: MultiSelect, value: object) -> Answer:
    """A structured selection: the ids named, in OPTION order. `[]` is the
    empty selection, a real answer; null is `abstained` only with
    `allow_none`; a count outside `min` to `max` is `unreadable`, never
    repaired."""
    if value is None:
        return Answer(None, "abstained") if q.allow_none else _UNREADABLE
    ids = _read_ids(q.options, value)
    if isinstance(ids, Answer):
        return ids
    if not q.min <= len(ids) <= q.most:
        return _UNREADABLE
    named = set(ids)
    return Answer(tuple(opt.id for opt in q.options if opt.id in named))


def _read(q: Question, answers: dict[str, Any]) -> Answer:
    if q.id not in answers:
        return _UNREADABLE
    value = answers[q.id]
    if isinstance(q, Predicate):
        return Answer(value) if isinstance(value, bool) else _UNREADABLE
    if isinstance(q, Choice):
        return _read_choice(q, value)
    if isinstance(q, Rank):
        return _read_rank(q, value)
    if isinstance(q, MultiSelect):
        return _read_select(q, value)
    if isinstance(q, Joint):
        return _joint_answer(_read_choice(q.choice, value))
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
            props, text = _tally(schema([*held, item], explain=True))
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


@dataclass(frozen=True)
class Lift:
    """How `native_form` lowered an item: per question id of the original,
    in order, the ids of the lowered questions that answer it."""

    parts: tuple[tuple[str, tuple[str, ...]], ...]


def _lowered(q: Question) -> tuple[Question, ...]:
    """The questions a decisions endpoint is asked in `q`'s place. A lowered
    predicate's id is `f"{q.id}#{index}"`. A rank with no `pointwise` is
    left as itself: `native_gap` refuses it before anything is built or
    sent."""
    if isinstance(q, Joint):
        return (q.choice,)
    if isinstance(q, Rank) and q.pointwise.strip():
        return tuple(Predicate(f"{q.id}#{n}",
                               f"{q.pointwise}\n\nCandidate {opt.id}: {opt.description}")
                     for n, opt in enumerate(q.candidates))
    if isinstance(q, MultiSelect):
        return tuple(Predicate(f"{q.id}#{n}",
                               NATIVE_SELECT_TEXT.format(instructions=q.instructions,
                                                         id=opt.id,
                                                         description=opt.description))
                     for n, opt in enumerate(q.options))
    return (q,)


def _marginals(keys: Sequence[str], got: Sequence[Answer]) -> dict[str, float] | None:
    """Each key's reported P(true), read off its lowered predicate's
    `Answer` as `native_answer` produced it (never re-thresholded); None when
    no predicate reported one."""
    out = {key: a.probability for key, a in zip(keys, got, strict=True)
           if a.probability is not None}
    return out or None


def _pointwise(got: Sequence[Answer], marginals: dict[str, float] | None,
               keys: Sequence[str]) -> Answer | None:
    """The failure a set of lowered predicates reads as, or None when every
    one reported a usable probability: every one refused is `refused`, and
    any one without a probability -- refused, unreadable or missing -- is
    `unreadable`, with the probabilities that were reported riding in
    `marginals`. A partial order, or a selection with an option unknown, is
    not what the caller asked for, and the missing place cannot be guessed."""
    if all(a.reason == "refused" for a in got):
        return Answer(None, "refused", marginals=marginals)
    if marginals is None or any(key not in marginals for key in keys):
        return Answer(None, "unreadable", marginals=marginals)
    return None


def _lift_rank(q: Rank, got: Sequence[Answer]) -> Answer:
    """A native rank: the candidates in tiers by P(true) under `pointwise`
    (`tiers`, so only values within `MASS_TIE` tie), `rest` empty -- the
    endpoint scored every candidate, so `top` holds by construction. A P(true)
    of exactly 0.5 places its candidate like any other value: `allow_none`
    has no native trigger, so a native rank never abstains."""
    keys = [opt.id for opt in q.candidates]
    marginals = _marginals(keys, got)
    failed = _pointwise(got, marginals, keys)
    if failed is not None or marginals is None:
        return failed or Answer(None, "unreadable")
    return Answer(Ranking(tiers=tiers({key: marginals[key] for key in keys})),
                  marginals=marginals)


def _lift_select(q: MultiSelect, got: Sequence[Answer]) -> Answer:
    """A native selection: every option whose predicate answered True, in
    option order, `marginals` riding. An option at exactly 0.5 (its predicate
    abstained) cannot be placed: `abstained` with `allow_none`, else
    `unreadable`, as a native choice keeps a none it was not allowed. A
    selection outside `min` to `max` is `unreadable`, never repaired by
    taking the top or padding: a caller that wants top-k by probability reads
    `marginals` and says so in its own code."""
    keys = [opt.id for opt in q.options]
    marginals = _marginals(keys, got)
    failed = _pointwise(got, marginals, keys)
    if failed is not None:
        return failed
    if any(a.reason == "abstained" for a in got):
        return Answer(None, "abstained" if q.allow_none else "unreadable",
                      marginals=marginals)
    selected = tuple(key for key, a in zip(keys, got, strict=True) if a.answer is True)
    if not q.min <= len(selected) <= q.most:
        return Answer(None, "unreadable", marginals=marginals)
    return Answer(selected, marginals=marginals)


def native_form(item: Item) -> tuple[Item, Lift]:
    """`item` as a decisions endpoint can be asked it, and how to read the
    reply back (`native_lift`). Neither endpoint has a rank, a selection or a
    joint question, so both adapters send what this lowers them to: a
    predicate, a choice and a score pass through unchanged under their own
    ids; a joint becomes its one flattened choice under its id; and a rank
    (with `pointwise`) or a selection becomes one predicate per candidate or
    option, all in the one request, since each question there is answered
    alone. An item
    of the three plain kinds comes back as itself, so its body is the one it
    always was."""
    parts = tuple((q, _lowered(q)) for q in item.questions)
    if all(low == (q,) for q, low in parts):
        return item, Lift(tuple((q.id, (q.id,)) for q in item.questions))
    return (Item(item.context, tuple(low for _, lows in parts for low in lows)),
            Lift(tuple((q.id, tuple(low.id for low in lows)) for q, lows in parts)))


def native_lift(item: Item, lowered: ItemResult, lift: Lift) -> ItemResult:
    """`lowered`, the reply to `native_form(item)`'s item as the adapter read
    it, as `item`'s own result: each question's answer from the lowered
    questions `lift` names for it -- a joint's flattened choice split back
    into its `Pair`, the distribution kept by the flattened key; a rank's or
    a selection's predicates read into a `Ranking` or a selection with their
    `marginals` (`_lift_rank`, `_lift_select`) -- with the backend and
    rationale as they were."""
    parts = dict(lift.parts)
    answers: dict[str, Answer] = {}
    for q in item.questions:
        got = [lowered.answers.get(qid, _UNREADABLE) for qid in parts.get(q.id, (q.id,))]
        if isinstance(q, Joint):
            answers[q.id] = _joint_answer(got[0])
        elif isinstance(q, Rank) and q.pointwise.strip():
            answers[q.id] = _lift_rank(q, got)
        elif isinstance(q, MultiSelect):
            answers[q.id] = _lift_select(q, got)
        else:
            answers[q.id] = got[0]
    return ItemResult(answers, rationale=lowered.rationale, backend=lowered.backend)


def native_gap(item: Item) -> str:
    """`""` when a decisions endpoint can carry `item`; otherwise one sentence
    naming what it cannot (ruling 25), which the native call refuses unsent.
    Today that is a choice whose options, with the reserved none, pass
    `NATIVE_MAX_OPTIONS` (a joint's flattened choice included); a rank that
    names no `pointwise` question, which no default wording stands in for;
    and an item whose lowered question ids collide (`native_form`). It runs
    on the ORIGINAL item, so each sentence names the caller's question ids.
    A limit is named here, never met by truncating."""
    for q in item.questions:
        if isinstance(q, Rank) and not q.pointwise.strip():
            return (f"Question {q.id} ranks its candidates and names no pointwise "
                    f"question; a decisions endpoint cannot order them.")
        choice = q.choice if isinstance(q, Joint) else q
        if isinstance(choice, Choice) and (
                n := len(native_choice_keys(choice))) > NATIVE_MAX_OPTIONS:
            noun = "legal pairs" if isinstance(q, Joint) else "options"
            return (f"Question {q.id} offers {n} {noun} including none; "
                    f"a decisions endpoint takes at most {NATIVE_MAX_OPTIONS}.")
    # A lowered question's id must be unique in the lowered item: a collision
    # is a native-only problem (a structured stage can still answer the item),
    # so it is refused unsent here rather than by `validate`.
    owner: dict[str, str] = {}
    for qid, lows in native_form(item)[1].parts:
        for low in lows:
            if low in owner:
                return (f"Questions {owner[low]} and {qid} are both asked as {low} "
                        f"once lowered; a decisions endpoint cannot tell their "
                        f"answers apart.")
            owner[low] = qid
    return ""


def _finite(value: object) -> bool:
    """Whether `value` is a finite real number: an int (never a bool), which
    is always finite however large -- so `math.isfinite` never has to convert
    one too large for a float -- or a finite float."""
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and math.isfinite(value))


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
      distribution's argmax under the same tie rule; else `unreadable`. Its
      `expected` level rides beside it (`_expected`), on an abstained tie
      too, and never a provider's own weighted `score`.
    """
    if refused:
        return Answer(None, "refused")
    p = _probability(probability)
    dist = _distribution(q, distribution)
    if isinstance(q, Predicate):
        value, reason, detail = _native_predicate(chosen, p)
    elif isinstance(q, Choice):
        value, reason, detail = _native_choice(q, chosen, dist)
    elif isinstance(q, Score):
        value, reason, detail = _native_score(q, chosen, dist)
    else:
        raise TypeError(f"a decisions endpoint asks no {q.KIND}; lower it first")
    stated = chosen if detail == NOT_AN_OPTION and isinstance(chosen, str) else ""
    expected = _expected(dist) if isinstance(q, Score) else None
    return Answer(value, reason, probability=p, distribution=dist, detail=detail,
                  expected=expected, stated=stated)


def _expected(dist: dict[str, float] | None) -> float | None:
    """A score's probability-weighted level, `sum(i * p_i) / sum(p_i)` over a
    validated distribution keyed `str(i)` (01e-C2): normalised by the mass
    reported, because a report may omit levels and an omitted level is
    unreported, not zero. None with no distribution or no mass. Computed from
    the distribution rather than read off a provider's `score` field, so one
    rule holds for both providers."""
    if not dist:
        return None
    mass = sum(dist.values())
    if mass <= 0:
        return None
    return sum(int(key) * weight for key, weight in dist.items()) / mass


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


def tiers(values: Mapping[str, float | int], *,
          tolerance: float = MASS_TIE) -> tuple[tuple[str, ...], ...]:
    """Keys grouped by value, highest first (01e-C2): the one grouping rule
    for every ordered signal -- a native rank's P(true), a per-candidate
    score's level or `expected`, a predicate's probability. Never breaks a
    tie.

    Greedy from the top, anchored on each tier's HIGHEST value: a key joins
    the current tier when its value lies within `tolerance` of that tier's
    first, so three values each just inside tolerance of the next cannot
    chain a long run into one tier. Within a tier, keys keep the mapping's
    order. The default tolerance is `MASS_TIE`, float error in a sum of
    reported probabilities; it applies to integer levels alike, so two
    candidates answered the same level are one tier. A value that is not a
    finite number (a bool included) is a `ValueError`: it has no place in
    an order."""
    if not (_finite(tolerance) and tolerance >= 0):
        raise ValueError("a tie tolerance is a finite number of at least 0")
    for key, value in values.items():
        if not _finite(value):
            raise ValueError(f"{key!r} has no finite value to order by")
    place = {key: n for n, key in enumerate(values)}
    out: list[tuple[str, ...]] = []
    tier: list[str] = []
    anchor = 0.0
    for key in sorted(values, key=lambda k: -values[k]):
        if tier and anchor - values[key] <= tolerance:
            tier.append(key)
            continue
        if tier:
            out.append(tuple(sorted(tier, key=place.__getitem__)))
        tier, anchor = [key], values[key]
    if tier:
        out.append(tuple(sorted(tier, key=place.__getitem__)))
    return tuple(out)


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


def head_marginal(answer: Answer, q: Joint) -> dict[str, float] | None:
    """A joint answer's distribution summed per head, in `q`'s head order,
    `NONE_KEY` excluded; None without a distribution (every structured
    answer)."""
    if not answer.distribution:
        return None
    mass: dict[str, float] = {}
    for key, weight in answer.distribution.items():
        if key != NONE_KEY:
            head = split_joint(key).head
            mass[head] = mass.get(head, 0.0) + weight
    order = [h.id for h in q.heads]
    return {head: mass[head] for head in sorted(mass, key=lambda h: (
        order.index(h) if h in order else len(order)))}


def head_first(answer: Answer, q: Joint) -> str | None:
    """`regrouped`'s rule applied to a joint answer's key, grouped by head,
    `NONE_KEY` excluded: the head the distribution puts most mass on when
    that is not the chosen pair's head, else None (and None with no chosen
    pair or no distribution).

    For the reason `regrouped` exists: an endpoint scoring pairs one by one
    splits a head's mass across its targets, so 0.3 on each of two strikes
    and 0.4 on one heal makes the argmax pair a heal while the action most
    mass favours is a strike. Which reading to trust is the caller's
    policy."""
    if not isinstance(answer.answer, Pair):
        return None
    keyed = Answer(answer.answer.key, distribution=answer.distribution)
    return regrouped(keyed, lambda key: split_joint(key).head, [h.id for h in q.heads])


def _present(record: dict[str, Any]) -> dict[str, Any]:
    """`record` without its keys whose value is empty or None."""
    return {key: value for key, value in record.items()
            if value is not None and value != "" and value != {} and value != []}


def _spelled(value: object) -> object:
    """An answer value as JSON: a `Ranking` as `{"tiers": [[...]], "rest":
    [...]}`, a `Pair` as its flattened key (never a two-element list, which a
    capture could not tell from a selection), a selection as its list,
    anything else as itself."""
    if isinstance(value, Pair):
        return value.key
    if isinstance(value, Ranking):
        return {"tiers": [list(tier) for tier in value.tiers], "rest": list(value.rest)}
    if isinstance(value, tuple):
        return list(value)
    return value


def _rendered(value: object) -> object:
    """An answer value as the structured reply would have written it: a
    selection as its list, a `Pair` as its flattened key, a ranking of
    singleton tiers as its id list, and a ranking with a tie as
    null, since no structured reply can state one (a grader reads the native
    result itself, so the null is never scored as a model's answer)."""
    if isinstance(value, Pair):
        return value.key
    if isinstance(value, Ranking):
        if any(len(tier) > 1 for tier in value.tiers):
            return None
        return [tier[0] for tier in value.tiers]
    if isinstance(value, tuple):
        return list(value)
    return value


def outcome(mode: str, provider: str, model: str,
            results: Sequence[ItemResult] | None = None, error: str = "") -> dict[str, Any]:
    """The capture's record of one call (spec §9.4): its mode and what served
    it, then each item's backend, normalised answers -- `answer`, always
    present (None as null), with its `reason`, `detail`, `probability`,
    `distribution`, `marginals` and `expected`, a `Ranking` as its tiers and
    rest, a `Pair` as its key and a selection as a list -- and rationale; or, on a failure, the `error`. A key
    whose value is empty or None is left out (an answer's own `answer`
    excepted)."""
    head = _present({"mode": mode, "provider": provider, "model": model})
    if error:
        return {**head, "error": error}
    items = []
    for result in results or ():
        answers = {
            qid: {"answer": _spelled(a.answer), **_present({
                "reason": a.reason, "detail": a.detail, "probability": a.probability,
                "distribution": dict(a.distribution) if a.distribution else None,
                "marginals": dict(a.marginals) if a.marginals else None,
                "expected": a.expected})}
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
            q.id: (_rendered(result.answers[q.id].answer) if q.id in result.answers else None)
            for q in item.questions}}
        if explain:
            entry["rationale"] = result.rationale
        out[str(index)] = entry
    return json.dumps(out)
