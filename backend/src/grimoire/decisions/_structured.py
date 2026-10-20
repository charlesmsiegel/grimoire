"""Read structured replies while distinguishing absent items from invalid answers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from .. import schemas
from . import _models, _schemas
from ._models import Answer as Answer
from ._models import Choice as Choice
from ._models import Item as Item
from ._models import ItemResult as ItemResult
from ._models import MultiSelect as MultiSelect
from ._models import Option as Option
from ._models import Question as Question
from ._models import Rank as Rank

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
    keys = ("rationale", *(k for k in _models.LEGACY_RATIONALE_KEYS if k not in asked))
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


_UNREADABLE = _models.Answer(None, "unreadable")
_NO_OBJECT = _models.Answer(None, "unreadable", detail=_models.NO_OBJECT)
_NO_ITEM = _models.Answer(None, "unreadable", detail=_models.NO_ITEM)


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
    return answer.reason == "unreadable" and answer.detail in ("", _models.NOT_AN_OPTION)


def held_no_object(answer: Answer | None) -> bool:
    """Whether `answer` says its reply held no JSON object at all (`NO_OBJECT`).

    The other side of `was_read`, for the same mappings: a batch none of whose
    deciding answers was read is undecodable only when every one of them held
    no object -- an object that holds no item is a reply that answered
    nothing, not one nobody could read. None (no such answer) is not."""
    return answer is not None and answer.detail == _models.NO_OBJECT


def _option_named(options: Sequence[Option], value: object) -> str | None:
    """The id of the option `value` names -- exactly first, then through
    `normalise` and aliases -- or None when it names none (or is not a
    string)."""
    if not isinstance(value, str):
        return None
    for opt in options:
        if value == opt.id:
            return opt.id
    key = _schemas.normalise(value)
    for opt in options:
        if key in {_schemas.normalise(s) for s in (opt.id, *opt.aliases)}:
            return opt.id
    return None


_NOT_AN_OPTION = _models.Answer(None, "unreadable", detail=_models.NOT_AN_OPTION)


def _read_choice(q: Choice, value: object) -> Answer:
    if value is None:
        return _models.Answer(None, "abstained") if q.allow_none else _UNREADABLE
    named = _option_named(q.options, value)
    return _NOT_AN_OPTION if named is None else _models.Answer(named)


def _read_listed(options: Sequence[Option], value: object) -> list[str] | Answer:
    """The option ids a list-valued answer names, in the order listed; or the
    `Answer` it reads as when it cannot be one: a value that is not a list
    is `unreadable`; an entry naming no option is `NOT_AN_OPTION` (with no
    `stated`, as a structured choice sets none); an entry naming an option
    already listed is `unreadable` -- the reply gave a value, and which place
    was meant cannot be told. No entry is dropped: dropping one would move
    every later one up a place, and an answer left unread is better than one
    misread (`_foreign_index`'s rule, for entries)."""
    if not isinstance(value, list):
        return _UNREADABLE
    named = [_option_named(options, entry) for entry in value]
    if any(n is None for n in named):
        return _NOT_AN_OPTION
    ids = [n for n in named if n is not None]
    if len(set(ids)) != len(ids):
        return _UNREADABLE
    return ids


def _read_rank(q: Rank, value: object) -> Answer:
    """A ranking, best first: every entry a candidate, each at most once, and
    at least `top` of them (all, with no `top`) -- else `unreadable`, never
    repaired. The listed candidates are singleton tiers, and `rest` holds the
    unlisted ones in input order."""
    if value is None:
        return _models.Answer(None, "abstained") if q.allow_none else _UNREADABLE
    listed = _read_listed(q.candidates, value)
    if isinstance(listed, _models.Answer):
        return listed
    if len(listed) < (len(q.candidates) if q.top is None else q.top):
        return _UNREADABLE
    rest = tuple(opt.id for opt in q.candidates if opt.id not in listed)
    return _models.Answer(_models.Ranking(tiers=tuple((c,) for c in listed), rest=rest))


def _lift_joint(answer: Answer) -> Answer:
    """A joint's flattened-choice answer as the joint's: an option id split
    back into its `Pair`; anything else -- an answer of None, with whatever
    report rides on it (a native distribution stays keyed by the flattened
    keys) -- as it is."""
    if isinstance(answer.answer, str):
        return replace(answer, answer=_models.split_joint(answer.answer))
    return answer


def _read_select(q: MultiSelect, value: object) -> Answer:
    """A selection: every entry an option, each at most once, from `min` to
    `max` of them -- else `unreadable`, never truncated or padded. The answer
    is the chosen ids in OPTION order, and `[]` is `()`, a real answer."""
    if value is None:
        return _models.Answer(None, "abstained") if q.allow_none else _UNREADABLE
    listed = _read_listed(q.options, value)
    if isinstance(listed, _models.Answer):
        return listed
    if not q.min <= len(listed) <= (len(q.options) if q.max is None else q.max):
        return _UNREADABLE
    return _models.Answer(tuple(opt.id for opt in q.options if opt.id in listed))


def _read(q: Question, answers: dict[str, Any]) -> Answer:
    if q.id not in answers:
        return _UNREADABLE
    value = answers[q.id]
    if isinstance(q, _models.Predicate):
        return _models.Answer(value) if isinstance(value, bool) else _UNREADABLE
    if isinstance(q, _models.Choice):
        return _read_choice(q, value)
    if isinstance(q, _models.Rank):
        return _read_rank(q, value)
    if isinstance(q, _models.MultiSelect):
        return _read_select(q, value)
    if isinstance(q, _models.Joint):
        return _lift_joint(_read_choice(q.choice, value))
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(q.levels):
        return _models.Answer(value)
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
            results.append(_models.ItemResult({q.id: _NO_OBJECT for q in item.questions}))
            continue
        found = _item_object(obj, index, items)
        answers, rationale = found if found is not None else (None, None)
        if not isinstance(answers, dict):
            results.append(_models.ItemResult({q.id: _NO_ITEM for q in item.questions}))
            continue
        note = rationale.strip() if explain and isinstance(rationale, str) else ""
        results.append(_models.ItemResult({q.id: _read(q, answers) for q in item.questions}, note))
    return tuple(results)


def unanswered(items: Sequence[Item], reason: str) -> tuple[ItemResult, ...]:
    """Every answer of every item `None` with `reason`."""
    return tuple(_models.ItemResult({q.id: _models.Answer(None, reason) for q in item.questions})
                 for item in items)
