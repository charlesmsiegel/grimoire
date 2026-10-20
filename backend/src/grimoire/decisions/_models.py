"""Closed questions, reported answers, and their immutable result contracts."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Final

from .. import schemas

log = logging.getLogger('grimoire.decisions')

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

#: Candidates one `Rank` may order (01e §4.1), argued from structure alone:
#: the structured reply echoes each candidate id, so it grows with the count,
#: and a long permutation is where duplicates and omissions appear; the native
#: lowering puts one question per candidate into one request, and neither
#: endpoint documents a questions-per-request bound (01 Appendix B), so a 413
#: would be the only sign of one. To be tuned against real prompts with 01a's
#: reports -- never against a measured library.
MIN_RANK_CANDIDATES, MAX_RANK_CANDIDATES = 2, 32

#: Options one `MultiSelect` may offer (01e §6.1), argued as
#: `MAX_RANK_CANDIDATES` is: a native multi-select is one predicate per
#: option in one request. Tuned later, the same way.
MIN_SELECT_OPTIONS, MAX_SELECT_OPTIONS = 1, 32

#: What joins a joint's head and tail in the pair's one key (01e §6.2):
#: ASCII, and holding no space or hyphen, which `normalise` would rewrite.
#: `validate` refuses it inside a head or tail id, so a key splits one way.
JOINT_SEP = "=>"

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
#: (`schemas.MAX_DEPTH`) cannot be reached: a batch schema is at most five
#: levels deep (batch, item, answers, a question's value, and a ranking's
#: array item) whatever it is asked.
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

#: How a multi-select's option is asked of a decisions endpoint, one predicate
#: per option (01e §6.1). A fixed wording is honest here, as it would not be
#: for a rank: "is this option selected" is exactly the per-option question a
#: multi-select asks. Filled with `str.format`, so the caller's text goes in
#: as values and is never read as a template.
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


#: Each question class names its kind in a `KIND` class constant -- neither a
#: constructor nor an equality field -- and the decide templates branch on it
#: (`decide/user.j2`), never on which attributes a question happens to have:
#: a new type with `options` would otherwise render as a choice.

@dataclass(frozen=True)
class Predicate:
    """A yes/no question; answered with a `bool`."""

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
    #: What the null means for THIS question, as a native endpoint describes
    #: its reserved none option (`none_text`); "" is `NATIVE_NONE_TEXT`.
    none_text: str = ""


def none_text(q: Choice) -> str:
    """The description a native endpoint is sent for `q`'s reserved none
    option: the question's own (the speaker pick's "control returns to the
    player"), else `NATIVE_NONE_TEXT`."""
    return q.none_text or NATIVE_NONE_TEXT


@dataclass(frozen=True)
class Score:
    """Place the text on an ordered scale; answered with a level's index."""

    KIND: ClassVar[str] = "score"

    id: str
    instructions: str
    levels: tuple[str, ...]


@dataclass(frozen=True)
class Rank:
    """Order the `candidates`, best first; answered with a `Ranking`.

    `top` asks for at least that many ranked (None: all of them); a reply
    that ranks fewer is unreadable, never padded. With `allow_none`, an
    explicit null means "cannot order these" (`abstained`). `pointwise` is a
    yes/no question asked of ONE candidate, which only a native stage uses
    (01e §4.3): a decisions endpoint has no ranking, and there is no default
    wording, because only the caller knows what per-candidate question its
    order stands for. A rank without one is refused unsent on a native stage
    (`native_gap`) and moves on down the chain."""

    KIND: ClassVar[str] = "rank"

    id: str
    instructions: str
    candidates: tuple[Option, ...]
    top: int | None = None
    allow_none: bool = False
    pointwise: str = ""


@dataclass(frozen=True)
class MultiSelect:
    """Pick any subset of `options`, from `min` to `max` of them (`max` None:
    up to all; `max=0` is zero, not "all"); answered with the chosen ids as a
    tuple in the caller's OPTION order -- a set has no order a model can be
    trusted to mean, and a consumer that fans an effect out in selection
    order needs one it can replay. The empty selection `()` is a real answer
    ("none of these"); `None` with `abstained` is "cannot say", and only
    `allow_none` permits it. A consumer must never read one as the other."""

    KIND: ClassVar[str] = "select"

    id: str
    instructions: str
    options: tuple[Option, ...]
    min: int = 0
    max: int | None = None
    allow_none: bool = False


def joint_key(head: str, tail: str | None) -> str:
    """A pair's one key: `head` for a head that takes no tail, else
    `head + JOINT_SEP + tail` -- the option id, the distribution key, and
    every capture's and render's spelling of the pair."""
    return head if tail is None else f"{head}{JOINT_SEP}{tail}"


@dataclass(frozen=True)
class Pair:
    """A `Joint`'s answer: the head (the action) and its tail (the target),
    None for a head that takes no tail. `key` is its one spelling, so a pair
    and its key cannot disagree; a pair is never written as a two-element
    list, which a capture could not tell from a selection."""

    head: str
    tail: str | None

    @property
    def key(self) -> str:
        return joint_key(self.head, self.tail)


def split_joint(key: str) -> Pair:
    """`joint_key`'s inverse. Raises `ValueError` for a key with no head, or a
    separator with no tail after it."""
    head, sep, tail = key.partition(JOINT_SEP)
    if not head or (sep and not tail):
        raise ValueError(f"{key!r} is not a joint pair's key")
    return Pair(head, tail if sep else None)


@dataclass(frozen=True)
class Joint:
    """One action and a target conditioned on it, in ONE question; answered
    with a `Pair`.

    `tails` maps a head id to its legal targets; a head missing from it, or
    mapped to an empty tuple, takes no tail. The conditioning IS the legal
    pair set: the caller lists only pairs that are legal, and the model never
    sees an illegal one. On both paths a joint is one flattened `Choice` over
    those pairs (`choice`): asked as two questions, a native endpoint would
    answer the target without seeing the action, since it answers each
    question alone (the 01 spec's identity lesson). With `allow_none`, null
    is `abstained`.
    More legal pairs than a choice carries is a two-step decision for the
    caller (a `Choice` of action, then a `Choice` or `MultiSelect` of its
    targets), never one joint."""

    KIND: ClassVar[str] = "joint"

    id: str
    instructions: str
    heads: tuple[Option, ...]
    tails: tuple[tuple[str, tuple[Option, ...]], ...]
    allow_none: bool = False

    @property
    def choice(self) -> Choice:
        """The flattened choice this joint is asked as (`joint_choice`)."""
        return joint_choice(self)


def joint_choice(q: Joint) -> Choice:
    """`q` as one `Choice` under its own id: per head, in order, one option per
    legal target -- keyed `joint_key(head, tail)` and described
    `"<head> -> <tail>"` -- or the head alone when it takes no tail."""
    targets = dict(q.tails)
    options: list[Option] = []
    for head in q.heads:
        tails = targets.get(head.id, ())
        if not tails:
            options.append(Option(head.id, head.description))
        options.extend(Option(joint_key(head.id, tail.id),
                              f"{head.description} -> {tail.description}")
                       for tail in tails)
    return Choice(q.id, q.instructions, tuple(options), allow_none=q.allow_none)


Question = Predicate | Choice | Score | Rank | MultiSelect | Joint

#: What a decisions endpoint can be asked: the three types both providers
#: have (01 Appendix B). Every other question is lowered to these
#: (01e §3.3) or refused unsent (`native_gap`).
NativeQuestion = Predicate | Choice | Score


@dataclass(frozen=True)
class Ranking:
    """A `Rank`'s answer: `tiers`, best first, whose members are tied, and
    `rest`, the candidates the answer did not rank, in input order.

    A ranking with ties and a flat order are different facts, so nothing here
    flattens silently: `flat` is the only flattening, and it takes the
    tie-break as an argument. `rest` is not a tier -- it is the explicit
    unranked bottom group a `top` let the model stop before -- so `flat`
    leaves it out, and a caller that wants it appends it in an order it
    names."""

    tiers: tuple[tuple[str, ...], ...]
    rest: tuple[str, ...] = ()

    def flat(self, tiebreak: Callable[[str], Any] | None = None) -> tuple[str, ...]:
        """The ranked candidates in one order, best first: each tier sorted by
        `tiebreak` (a sort key). Raises `ValueError` on a tier of more than
        one with no tie-break, rather than breaking the tie by position."""
        out: list[str] = []
        for tier in self.tiers:
            if len(tier) > 1:
                if tiebreak is None:
                    raise ValueError(f"a tier ties {list(tier)}; flattening it needs a "
                                     f"tie-break")
                out.extend(sorted(tier, key=tiebreak))
            else:
                out.extend(tier)
        return tuple(out)

    def position(self, candidate: str) -> int | None:
        """The index of `candidate`'s tier; None for a candidate in `rest`
        and for one the ranking does not hold."""
        return next((n for n, tier in enumerate(self.tiers) if candidate in tier), None)


@dataclass(frozen=True)
class Item:
    """One thing to decide about: its own `context`, and the questions asked of
    it, in order."""

    context: str
    questions: tuple[Question, ...]


def kinds(items: Sequence[Item]) -> tuple[str, ...]:
    """The `KIND`s `items` ask, each once, first asked first: what
    `decide/system.j2` explains, so a bullet for a kind renders only beside a
    question of it, and no existing call site's prompt moves."""
    return tuple(dict.fromkeys(q.KIND for item in items for q in item.questions))


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

    `marginals` are per-key probabilities a native endpoint reported
    INDEPENDENTLY -- each candidate's or option's P(true) under its lowered
    predicate (01e) -- so they do not sum to 1, and are never a
    `distribution`: a sampler that took them for one would sample a
    meaningless value. `expected` is a native score's probability-weighted
    level over its reported distribution (`native_answer`). Both ride on an
    answer of None too, and both are None on every structured answer.
    `expected` is not compared: it is a reading of `distribution`, which is.
    """

    answer: bool | str | int | Ranking | Pair | tuple[str, ...] | None
    reason: str = ""
    probability: float | None = None
    distribution: dict[str, float] | None = None
    detail: str = ""
    stated: str = field(default="", compare=False)
    marginals: dict[str, float] | None = None
    expected: float | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if (self.answer is None) != bool(self.reason):
            raise ValueError("an answer carries a reason exactly when it is None")
        if self.reason and self.reason not in REASONS:
            raise ValueError(f"unknown reason {self.reason!r}")
        if self.detail and self.reason != "unreadable":
            raise ValueError("a detail qualifies an unreadable answer only")
        if self.stated and self.detail != NOT_AN_OPTION:
            raise ValueError("a stated value qualifies an answer that named no option only")
        if self.marginals is not None and not (
                isinstance(self.marginals, Mapping)
                and all(isinstance(key, str) and _probability(p) is not None
                        for key, p in self.marginals.items())):
            raise ValueError("marginals map keys to probabilities")
        if self.expected is not None and _finite(self.expected) is None:
            raise ValueError("an expected level is a finite number")


@dataclass(frozen=True)
class ItemResult:
    """One item's answers, keyed by question id in question order, and its
    rationale (empty unless one was asked for and given).

    `backend` is the backend that answered this item (one of `BACKENDS`), the
    per-item truth when one batch is split across stages; `""` on an item
    nothing answered (`unanswered`) and on what `parse` returns, since the
    backend stamps it, not the parser.

    `served` is `(kind, provider id, model)` of the server of the ATTEMPT
    whose reply this result was read from -- its adapter kind, its provider
    id and the model it was sent -- stamped by the backend, as `backend` is,
    and `()` on an item nothing stamped (`unanswered`, `parse`, a result
    built by hand). It names the attempt, not a verdict that this item was
    answered: an item of a structured chunk the reply never reached
    (`unreadable` with `NO_ITEM` or `NO_OBJECT`) carries its chunk's server
    too. It is not compared, so two results that read alike are equal whoever
    answered them (01d §5.6). The escalation triggers read `served[0]` for
    the threshold (`triggers`) and `served[1:]` for the same-model skip; a
    sampler's record reads all three (01c)."""

    answers: dict[str, Answer]
    rationale: str = ""
    backend: str = ""
    served: tuple[()] | tuple[str, str, str] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        if self.backend and self.backend not in BACKENDS:
            raise ValueError(f"unknown backend {self.backend!r}")
        if self.served != () and not (
                isinstance(self.served, tuple) and len(self.served) == 3
                and all(isinstance(part, str) for part in self.served)):
            raise ValueError("served is () or (kind, provider_id, model)")


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
    chain itself, and `HOP_ESCALATION` for a call an escalation hop made.

    A row is what costs money, so this counts requests. The eval runner reads
    it, and so does the history rerank (`routes/history_recall.py`), which
    reads only `error_kind` to say why an unanswered rerank was skipped."""

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
    #: One per triggered item, in trigger priority order (01d §5.5): what an
    #: escalation hop did with it (`Escalation`). Empty when nothing
    #: escalated.
    escalations: tuple[Escalation, ...] = ()

    def __post_init__(self) -> None:
        if self.backend and self.backend not in BACKENDS:
            raise ValueError(f"unknown backend {self.backend!r}")


def _probability(value: object) -> float | None:
    """`value` as a probability: a finite number (never a bool) in [0, 1].
    The range is checked before anything converts it, so an int too large for
    a float (`10**400`) is out of range rather than an `OverflowError`; NaN
    fails both comparisons."""
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 <= value <= 1 and math.isfinite(value)):
        return float(value)
    return None


def _finite(value: object) -> float | None:
    """`value` as a finite real number (never a bool), or None -- an int too
    large for a float included."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


#: How far apart two summed masses may be and still tie in `regrouped`: a sum
#: of reported probabilities carries float error (0.1 + 0.2 is not 0.3), and
#: an error must not decide which of two equal meanings wins. `triggers`
#: reads a margin against its threshold within it too, for the same reason:
#: P = 0.6 has a margin of 0.19999999999999996, which is ON a 0.2 threshold.
MASS_TIE = 1e-9


# --- trigger evaluation (roadmap 01d-C2a) ------------------------------------

#: What hands an answered item to a second opinion (01d §5.1): a native
#: refusal, an abstention, and an answer its own report puts too narrowly
#: ahead of a rival. The same name and value as `routing.TRIGGERS` (01d-S1),
#: which this leaf may not import; `test_decision_triggers.py` pins the two.
TRIGGERS: tuple[str, ...] = ("low_margin", "abstained", "refused")

#: `triggers`' order: a refusal first, then an abstention, then low margins.
_PRIORITY = {"refused": 0, "abstained": 1, "low_margin": 2}


@dataclass(frozen=True)
class Trigger:
    """One item `triggers` found: its index in the batch, which of `TRIGGERS`
    it met, and -- for `low_margin`, and only for it -- its margin."""

    index: int
    trigger: str
    margin: float | None = None

    def __post_init__(self) -> None:
        if self.trigger not in TRIGGERS:
            raise ValueError(f"unknown trigger {self.trigger!r}")
        if (self.trigger == "low_margin") != (self.margin is not None):
            raise ValueError("a trigger carries a margin exactly when it is low_margin")
        if self.margin is not None and _finite(self.margin) is None:
            raise ValueError("a margin is a finite number")


# --- the escalation hop's record (roadmap 01d-C2b) ----------------------------

#: `CallRecord.hop`, a ledger row's `hop` and a capture outcome's `"hop"` for a
#: call an escalation hop made (01d §5.5, §5.7); "" for a call of the chain.
HOP_ESCALATION = "escalation"

#: What became of one triggered item (01d §5.5).
ESCALATION_OUTCOMES: tuple[str, ...] = ("answered", "failed", "skipped")

#: `Escalation.detail` words this module names (the rest are composed: a
#: failed call's ``"kind: detail"``, ``"unreadable: <detail>"``,
#: ``"unresolved: <kind>"`` and the escalator's own sentence).
SKIPPED_CAP = "cap"
SKIPPED_SAME_MODEL = "same_model"
SKIPPED_INCAPABLE = "incapable"
SKIPPED_DEAD_CONNECTION = "dead_connection"
FAILED_DECLINED = "declined"
FAILED_NO_RESULT = "no_result"


@dataclass(frozen=True)
class Escalation:
    """What one escalation hop did with one triggered item (01d §5.5): the
    item's batch `index`, the `trigger` it met (one of `TRIGGERS`) and -- for
    `low_margin` only -- its `margin`, the base answer it had (`before`), and
    the `outcome` (one of `ESCALATION_OUTCOMES`): `answered` (the hop's
    answer replaced the item's deciding answer), `failed` (the hop ran and
    the original stands) or `skipped` (no hop ran for it).

    `detail` says why, for every outcome but `answered` (""): skipped --
    `cap`, `same_model`, `incapable`, `dead_connection` (the base met a
    failure every call on the hop's connection would meet),
    ``unresolved: <kind>`` (the escalator raised; its kind, never its text),
    or the escalator's own sentence; failed -- `declined` (a hop decline on a
    task that does not read one), `no_result` (a caller resolver gave none),
    ``unreadable`` or ``unreadable: <detail>`` (a garbled hop answer), or a
    failed call's ``"kind: detail"``. **That last can carry a provider's own
    text, so `detail` is never persisted**: a capture or a log that keeps an
    escalation keeps the part before the first ``": "``.

    `served` is the hop's `(kind, provider id, model)`: the server of the
    answer that replaced the item, or the hop target a failed role hop was
    sent; `()` when skipped, or when nothing names one."""

    index: int
    trigger: str
    margin: float | None
    before: ItemResult
    outcome: str
    detail: str = ""
    served: tuple[()] | tuple[str, str, str] = ()

    def __post_init__(self) -> None:
        if self.trigger not in TRIGGERS:
            raise ValueError(f"unknown trigger {self.trigger!r}")
        if self.outcome not in ESCALATION_OUTCOMES:
            raise ValueError(f"unknown escalation outcome {self.outcome!r}")
        if (self.trigger == "low_margin") != (self.margin is not None):
            raise ValueError("an escalation carries a margin exactly when it is low_margin")
        if self.served != () and not (
                isinstance(self.served, tuple) and len(self.served) == 3
                and all(isinstance(part, str) for part in self.served)):
            raise ValueError("served is () or (kind, provider_id, model)")


@dataclass(frozen=True)
class ResolverReply:
    """What a caller-supplied escalation resolver returns (01d §5.2): one
    result per item it was handed, in that order -- an `ItemResult` whose
    `backend` is one of `BACKENDS` (a tool loop that ends in a generated
    answer stamps `structured`), whose questions are exactly the item's, and
    whose `served` is its final target's ``(kind, provider_id, model)`` -- or
    None where it produced nothing; and the ledger rows its calls filed, each
    carrying ``hop: "escalation"`` (`HOP_ESCALATION`)."""

    results: tuple[ItemResult | None, ...]
    rows: tuple[dict[str, Any], ...] = ()
