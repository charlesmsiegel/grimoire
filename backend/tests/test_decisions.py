"""The decide() contract: its types, validation, batch schema, parser and chunks.

`grimoire.decisions` is a leaf (it imports nothing from the package), so these
tests need no store and no client: every case is a pure function of its input.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from grimoire import decisions
from grimoire.decisions import (
    Answer,
    Choice,
    DecideRequestError,
    Item,
    ItemResult,
    Joint,
    MultiSelect,
    Option,
    Pair,
    Predicate,
    Rank,
    Ranking,
    Score,
)
from tests.test_llm import _sibling_imports

MARA = Option("characters:mara", "Mara")
WINIFRED = Option("characters:winifred", "Winifred")
GRIMOIRE = Option("grimoire", "g")


def _item(*, allow_none: bool = True) -> Item:
    return Item("ctx", (
        Predicate("over", "i"),
        Choice("who", "i", (MARA, GRIMOIRE), allow_none=allow_none),
        Score("tone", "i", ("low", "mid", "high")),
    ))


def _choice_item(options, *, allow_none: bool = False) -> Item:
    return Item("ctx", (Choice("q", "i", tuple(options), allow_none=allow_none),))


def _options(n: int) -> tuple[Option, ...]:
    return tuple(Option(f"o{i}", "") for i in range(n))


def _answers(text: str, items, *, explain: bool = True):
    return [r.answers for r in decisions.parse(text, items, explain=explain)]


def _unreadable(answer: Answer, detail: str = "") -> bool:
    return answer == Answer(None, "unreadable", detail=detail)


# --- validate ---------------------------------------------------------------

def test_validate_bounds():
    for n in (decisions.MIN_OPTIONS, decisions.MAX_OPTIONS):
        decisions.validate([_choice_item(_options(n))])
    for n in (1, 256):
        with pytest.raises(DecideRequestError):
            decisions.validate([_choice_item(_options(n))])
    decisions.validate([_choice_item(_options(1), allow_none=True)])
    with pytest.raises(DecideRequestError):
        decisions.validate([_choice_item(_options(256), allow_none=True)])

    decisions.validate([Item("ctx", (Score("s", "i", ("a",) * 2),))])
    decisions.validate([Item("ctx", (Score("s", "i", ("a",) * 10),))])
    for n in (1, 11):
        with pytest.raises(DecideRequestError):
            decisions.validate([Item("ctx", (Score("s", "i", ("a",) * n),))])

    with pytest.raises(DecideRequestError):
        decisions.validate([Item("ctx", (Predicate("p", "i"), Predicate("p", "j")))])
    for reserved in ("rationale", "answers", "0"):
        with pytest.raises(DecideRequestError):
            decisions.validate([Item("ctx", (Predicate(reserved, "i"),))])

    with pytest.raises(DecideRequestError):
        decisions.validate([])


def test_validate_rejects_empty_shapes():
    for bad in (
        [Item("ctx", ())],                                    # no questions
        [Item("ctx", (Predicate("", "i"),))],                 # empty question id
        [Item("ctx", (Predicate("12", "i"),))],               # all digits
        [_choice_item((Option("", ""), MARA))],               # empty option id
        [_choice_item((Option("x", "", ("",)), MARA))],       # empty alias
        [Item("ctx", ("over",))],                             # not a question
    ):
        with pytest.raises(DecideRequestError):
            decisions.validate(bad)  # type: ignore[arg-type]


def test_validate_allows_the_same_id_in_different_items():
    decisions.validate([Item("a", (Predicate("p", "i"),)), Item("b", (Predicate("p", "i"),))])


def test_validate_rejects_collisions_after_normalising():
    drift = Option("drift", "")
    for options in (
        (Option("not_enough", "", ("drift",)), drift),
        (Option("in voice", ""), Option("in_voice", "")),
        (Option("Characters:Mara", ""), Option("characters:mara", "")),
        (Option("not_enough", "", ("Not Enough",)), drift),   # an alias of its own id
        (Option("a", "", ("x",)), Option("b", "", ("X",))),   # alias against alias
    ):
        with pytest.raises(DecideRequestError):
            decisions.validate([_choice_item(options)])
    decisions.validate([_choice_item((Option("not_enough", "", ("insufficient",)), drift))])


def test_decide_request_error_is_a_value_error():
    assert issubclass(DecideRequestError, ValueError)


def test_normalise():
    assert decisions.normalise("  In Voice ") == "in_voice"
    assert decisions.normalise("not-enough") == "not_enough"
    assert decisions.normalise("not - enough") == "not_enough"
    assert decisions.normalise("STRASSE") == decisions.normalise("straße")


# --- schema -----------------------------------------------------------------

def test_schema_shape():
    item = _item()
    assert decisions.schema([item], explain=True) == {
        "type": "object", "additionalProperties": False, "required": ["0"],
        "properties": {"0": {
            "type": "object", "additionalProperties": False,
            "required": ["answers", "rationale"],
            "properties": {
                "answers": {"type": "object", "additionalProperties": False,
                            "required": ["over", "who", "tone"],
                            "properties": {
                                "over": {"type": "boolean"},
                                "who": {"anyOf": [
                                    {"type": "string", "enum": ["characters:mara", "grimoire"]},
                                    {"type": "null"}]},
                                "tone": {"type": "integer", "enum": [0, 1, 2]}}},
                "rationale": {"type": "string"}}}}}

    plain = decisions.schema([item], explain=False)["properties"]["0"]
    assert plain["required"] == ["answers"]
    assert set(plain["properties"]) == {"answers"}

    strict = decisions.schema([_item(allow_none=False)], explain=False)
    who = strict["properties"]["0"]["properties"]["answers"]["properties"]["who"]
    assert who == {"type": "string", "enum": ["characters:mara", "grimoire"]}


def test_schema_keys_items_by_index():
    items = [_item(), Item("other", (Predicate("p", "i"),))]
    out = decisions.schema(items, explain=False)
    assert out["required"] == ["0", "1"]
    assert list(out["properties"]) == ["0", "1"]
    assert out["properties"]["1"]["properties"]["answers"]["required"] == ["p"]


#: The JSON Schema keywords both OpenAI strict mode and Anthropic's
#: `output_config.format` document. Anything else (numeric bounds, `pattern`,
#: `description`, `$ref`, a type array) is a schema one provider may refuse.
SHARED_SUBSET = {"type", "enum", "anyOf", "required", "properties", "additionalProperties"}


def _walk(node, path="$"):
    assert isinstance(node, dict), path
    assert set(node) <= SHARED_SUBSET, (path, set(node) - SHARED_SUBSET)
    assert not isinstance(node.get("type"), list), path
    assert node.get("type") in {"object", "string", "boolean", "integer", "null", None}, path
    if "enum" in node:
        assert all(isinstance(v, (str, int)) for v in node["enum"]), path
    if node.get("type") == "object":
        assert node["additionalProperties"] is False, path
        assert node["required"] == list(node["properties"]), path
        for name, child in node["properties"].items():
            _walk(child, f"{path}.{name}")
    for i, branch in enumerate(node.get("anyOf", ())):
        _walk(branch, f"{path}|{i}")


def test_schema_uses_only_the_shared_subset():
    items = [
        _item(allow_none=True),
        _item(allow_none=False),
        _choice_item(_options(decisions.MAX_OPTIONS)),
        Item("ctx", (Score("s", "i", ("a",) * decisions.MAX_LEVELS),)),
    ]
    for explain in (True, False):
        _walk(decisions.schema(items, explain=explain))


def test_schema_is_json_serialisable():
    json.dumps(decisions.schema([_item()], explain=True))


# --- parse ------------------------------------------------------------------

REPLY = '{"0": {"answers": {"over": true, "who": "grimoire", "tone": 2}, "rationale": " r "}}'


def test_parse_reads_each_type():
    (result,) = decisions.parse(REPLY, [_item()], explain=True)
    assert result == ItemResult(
        {"over": Answer(True), "who": Answer("grimoire"), "tone": Answer(2)}, "r")
    assert list(result.answers) == ["over", "who", "tone"]


def test_parse_tolerates_fences_and_prose():
    expected = decisions.parse(REPLY, [_item()], explain=True)
    for text in (
        f"```json\n{REPLY}\n```",
        f"```\n{REPLY}\n```",
        f"  ```JSON\n{REPLY}```  ",
        f"Here you go: {REPLY} Thanks.",
        f"Here you go:\n```json\n{REPLY}\n```\nThanks.",
        # Only the fence finds this one: the first `{` to the last `}` spans
        # the trailing note too, and does not decode.
        f"```json\n{REPLY}\n```\nNote: {{not json}}",
    ):
        assert decisions.parse(text, [_item()], explain=True) == expected, text


def test_find_object_is_the_parsers_reading_of_the_reply():
    """Public so a grader can ask what `parse` cannot say: whether the reply
    held an object at all, as opposed to one with unreadable answers."""
    obj = json.loads(REPLY)
    for text in (REPLY, f"```json\n{REPLY}\n```", f"Here you go: {REPLY} Thanks."):
        assert decisions.find_object(text) == obj, text
    for text in ("", "no idea", "[1, 2, 3]", "null", REPLY[:-5]):
        assert decisions.find_object(text) is None, text


def test_parse_unwrapped_single_item():
    text = '{"answers": {"over": false, "who": "characters:mara", "tone": 0}, "rationale": "r"}'
    (result,) = decisions.parse(text, [_item()], explain=True)
    assert result == ItemResult(
        {"over": Answer(False), "who": Answer("characters:mara"), "tone": Answer(0)}, "r")

    for result in decisions.parse(text, [_item(), _item()], explain=True):
        assert result.rationale == ""
        assert all(_unreadable(a, decisions.NO_ITEM) for a in result.answers.values())


def test_parse_flattened_single_item():
    item = Item("ctx", (Predicate("over", "i"),))
    (result,) = decisions.parse('{"over": true, "rationale": "r"}', [item], explain=True)
    assert result == ItemResult({"over": Answer(True)}, "r")

    for result in decisions.parse('{"over": true, "rationale": "r"}', [item, item], explain=True):
        assert result == ItemResult({"over": Answer(None, "unreadable", detail=decisions.NO_ITEM)})

    (result,) = decisions.parse('{"unrelated": 1}', [item], explain=True)
    assert result == ItemResult({"over": Answer(None, "unreadable", detail=decisions.NO_ITEM)})


def test_parse_flattened_takes_the_legacy_rationale_keys():
    """A model answering in the legacy prompts' style names the rationale as
    those prompts asked: voice drift's `note`, scene-break's `reason`. The
    flattened single-item shape reads either as the rationale, so the
    corrective is not lost for being spelled the old way."""
    item = Item("ctx", (Predicate("over", "i"),))
    for key in ("note", "reason"):
        (result,) = decisions.parse(json.dumps({"over": True, key: " It ended. "}), [item],
                                    explain=True)
        assert result == ItemResult({"over": Answer(True)}, "It ended."), key
        # Only when a rationale was asked for, as with `rationale` itself.
        (result,) = decisions.parse(json.dumps({"over": True, key: "x"}), [item], explain=False)
        assert result.rationale == "", key


def test_the_legacy_rationale_keys_rank_below_rationale_and_never_widen():
    """`rationale` wins when present (even when it is not a string, which
    blanks it rather than falling through); `note` before `reason`; a
    non-string legacy key is blank. The aliases are the FLATTENED shape's
    only: a wrapped or unwrapped item, and a question whose own id is `note`,
    keep their meaning."""
    item = Item("ctx", (Predicate("over", "i"),))

    def note(obj: dict) -> str:
        (result,) = decisions.parse(json.dumps(obj), [item], explain=True)
        return result.rationale

    assert note({"over": True, "rationale": "r", "note": "n", "reason": "x"}) == "r"
    assert note({"over": True, "rationale": 42, "note": "n"}) == ""
    assert note({"over": True, "note": "n", "reason": "x"}) == "n"
    assert note({"over": True, "note": {"tone": "terse"}}) == ""
    assert note({"over": True, "note": None, "reason": "x"}) == ""
    for wrapped in ({"0": {"answers": {"over": True}, "note": "n"}},
                    {"answers": {"over": True}, "reason": "x"}):
        (result,) = decisions.parse(json.dumps(wrapped), [item], explain=True)
        assert result == ItemResult({"over": Answer(True)}, ""), wrapped
    asked = Item("ctx", (Predicate("over", "i"),
                         Choice("note", "i", (Option("a", "A"), Option("b", "B")))))
    (result,) = decisions.parse('{"over": true, "note": "a", "reason": "why"}', [asked],
                                explain=True)
    assert result == ItemResult({"over": Answer(True), "note": Answer("a")}, "why")


def test_parse_reads_an_indexed_item_with_the_answers_level_dropped():
    """CODE-M8: the item keyed by its index, its question answers directly
    under it (`{"0": {"over": true}}`) -- a plausible reply from a fallback
    without structured mode -- reads as the flattened shape does, rationale
    and its legacy aliases included, for one item or several."""
    item = Item("ctx", (Predicate("over", "i"),))
    (result,) = decisions.parse('{"0": {"over": true, "rationale": "r"}}', [item],
                                explain=True)
    assert result == ItemResult({"over": Answer(True)}, "r")
    (result,) = decisions.parse('{"0": {"over": false, "note": "n"}}', [item], explain=True)
    assert result == ItemResult({"over": Answer(False)}, "n")
    items = [_item(), Item("b", (Predicate("p", "i"),))]
    first, second = decisions.parse(
        json.dumps({"0": {"over": True, "who": "characters:mara", "tone": 1},
                    "1": {"answers": {"p": True}}}), items, explain=False)
    assert first.answers == {"over": Answer(True), "who": Answer("characters:mara"),
                             "tone": Answer(1)}
    assert second.answers == {"p": Answer(True)}
    # Still nothing to read when the entry names none of the item's questions.
    (result,) = decisions.parse('{"0": {"unrelated": true}}', [item], explain=True)
    assert result == ItemResult({"over": Answer(None, "unreadable", detail=decisions.NO_ITEM)})


def test_a_reply_numbered_from_one_answers_no_item():
    """Brutal-1 #3: two items whose questions share ids, answered `"1"`, `"2"`
    by a fallback without structured mode. Its `"1"` is item 0's answer, not
    item 1's -- so a reply carrying an index outside the items sent has keys
    that are not ours, and every item is left unread rather than any answer
    being guessed onto another item. Each reads `NO_ITEM`: the object was
    there, but nothing in it is ours to read as that item -- so `was_read` says
    no, and no mapping stores a safe default the model never gave."""
    items = [Item("a", (Predicate("over", "i"),)), Item("b", (Predicate("over", "i"),))]
    unread = ItemResult({"over": Answer(None, "unreadable", detail=decisions.NO_ITEM)})
    assert not decisions.was_read(unread.answers["over"])
    for reply in ({"1": {"answers": {"over": True}}, "2": {"answers": {"over": False}}},
                  {"0": {"answers": {"over": True}}, "1": {"answers": {"over": False}},
                   "2": {"answers": {"over": True}}},
                  {"1": {"over": True}, "2": {"over": False}},
                  {"01": {"answers": {"over": True}}, "1": {"answers": {"over": False}}}):
        assert decisions.parse(json.dumps(reply), items, explain=False) == (unread, unread), reply
    # One item: `{"1": ...}` was already nothing to read, and stays so.
    (result,) = decisions.parse('{"1": {"answers": {"over": true}}}', items[:1], explain=False)
    assert result == unread


def test_a_reply_whose_keys_are_all_in_range_is_read_as_before():
    """Every index key one we sent: read as today, an item left out included
    (it is `NO_ITEM`; the other is answered). The second reply is also the
    shape `_foreign_index`'s documented limit cannot tell from a reply keyed
    from 1 that skipped the last item -- pinned so that refusing it is a
    decision someone makes, not a side effect."""
    items = [Item("a", (Predicate("over", "i"),)), Item("b", (Predicate("over", "i"),))]
    first, second = decisions.parse(
        '{"0": {"answers": {"over": true}}, "1": {"answers": {"over": false}}}', items,
        explain=False)
    assert (first.answers["over"], second.answers["over"]) == (Answer(True), Answer(False))
    first, second = decisions.parse('{"1": {"answers": {"over": false}}}', items,
                                    explain=False)
    assert first.answers["over"] == Answer(None, "unreadable", detail=decisions.NO_ITEM)
    assert second.answers["over"] == Answer(False)


def test_parse_flattened_reads_what_it_holds():
    """A flattened reply that answers some questions leaves the rest unreadable."""
    (answers,) = _answers('{"over": true}', [_item()])
    assert answers["over"] == Answer(True)
    assert _unreadable(answers["who"]) and _unreadable(answers["tone"])


def test_parse_reads_several_items_in_order():
    items = [_item(), Item("b", (Predicate("p", "i"),))]
    text = json.dumps({"1": {"answers": {"p": False}},
                       "0": {"answers": {"over": True, "who": None, "tone": 1}}})
    first, second = decisions.parse(text, items, explain=False)
    assert first.answers == {"over": Answer(True), "who": Answer(None, "abstained"),
                             "tone": Answer(1)}
    assert second.answers == {"p": Answer(False)}


def test_parse_a_missing_item_is_unreadable():
    items = [_item(), Item("b", (Predicate("p", "i"),))]
    _, second = decisions.parse('{"0": {"answers": {"over": true}}}', items, explain=False)
    assert second == ItemResult({"p": Answer(None, "unreadable", detail=decisions.NO_ITEM)})


def test_parse_normalises_spelling_and_aliases():
    item = _choice_item((Option("drift", ""), Option("in_voice", ""),
                         Option("not_enough", "", ("insufficient", "unclear"))))

    def read(value):
        (answers,) = _answers(json.dumps({"0": {"answers": {"q": value}}}), [item])
        return answers["q"]

    assert read("In Voice") == Answer("in_voice")
    assert read("not-enough") == Answer("not_enough")
    assert read("insufficient") == Answer("not_enough")
    assert read(" UNCLEAR ") == Answer("not_enough")
    assert read("ok") == Answer(None, "unreadable", detail="not_an_option")


def test_unreadable_answers_carry_a_reason():
    def one(question, value):
        item = Item("ctx", (question,))
        (answers,) = _answers(json.dumps({"0": {"answers": {question.id: value}}}), [item])
        return answers[question.id]

    predicate = Predicate("over", "i")
    choice = Choice("who", "i", (MARA, WINIFRED))
    score = Score("tone", "i", ("low", "mid", "high"))

    assert _unreadable(one(predicate, "true"))
    assert _unreadable(one(choice, "characters:rowan"), decisions.NOT_AN_OPTION)
    assert _unreadable(one(choice, 5), decisions.NOT_AN_OPTION)
    assert _unreadable(one(choice, None))
    assert _unreadable(one(score, 3))
    assert _unreadable(one(score, -1))
    assert _unreadable(one(score, True))
    assert _unreadable(one(score, 1.0))

    (missing,) = _answers('{"0": {"answers": {}}}', [Item("ctx", (predicate,))])
    assert _unreadable(missing["over"])

    for text in ("[]", "no json here"):
        for answers in _answers(text, [_item()]):
            assert all(_unreadable(a, decisions.NO_OBJECT) for a in answers.values()), text


def test_a_reply_with_no_object_marks_every_answer():
    for text in ("no json here", "", "[]"):
        results = decisions.parse(text, [_item(), _item()], explain=False)
        assert len(results) == 2
        for result in results:
            assert result.answers
            assert all(a == Answer(None, "unreadable", detail=decisions.NO_OBJECT)
                       for a in result.answers.values()), text


def test_an_item_the_object_does_not_hold_is_no_item():
    items = [_item(), _item()]

    def no_item(text, index):
        answers = decisions.parse(text, items, explain=False)[index].answers
        assert answers
        return all(a == Answer(None, "unreadable", detail=decisions.NO_ITEM)
                   for a in answers.values())

    assert no_item('{"0": {"answers": {"over": true, "who": null, "tone": 1}}}', 1)
    assert no_item('{"0": 5}', 0)
    assert no_item('{"0": {"answers": []}}', 0)
    assert no_item("{}", 0) and no_item("{}", 1)


def test_a_question_missing_from_a_read_item_has_no_detail():
    (answers,) = _answers('{"0": {"answers": {}}}', [Item("ctx", (Predicate("over", "i"),))])
    assert answers["over"] == Answer(None, "unreadable")
    assert answers["over"].detail == ""


def test_a_choice_that_allows_none_may_offer_one_option():
    only = Option("find-the-ledger", "Find the ledger")
    item = Item("ctx", (Choice("id", "i", (only,), allow_none=True),))
    decisions.validate([item])
    assert decisions.schema([item], explain=False)["properties"]["0"]["properties"][
        "answers"]["properties"]["id"] == {
            "anyOf": [{"type": "string", "enum": ["find-the-ledger"]}, {"type": "null"}]}
    (read,) = _answers('{"0": {"answers": {"id": "find-the-ledger"}}}', [item])
    assert read["id"] == Answer("find-the-ledger")
    (none,) = _answers('{"0": {"answers": {"id": null}}}', [item])
    assert none["id"] == Answer(None, "abstained")


def test_a_choice_without_none_still_needs_two():
    with pytest.raises(DecideRequestError):
        decisions.validate([_choice_item(_options(1))])
    with pytest.raises(DecideRequestError):
        decisions.validate([_choice_item((), allow_none=True)])


def test_a_repeated_key_keeps_its_first_value():
    items = [Item("a", (Predicate("over", "i"),)), Item("b", (Predicate("over", "i"),))]
    text = ('{"0": {"answers": {"over": true}}, "1": {"answers": {"over": true}}, '
            '"0": {"answers": {"over": false}}}')
    for wrapped in (text, f"```json\n{text}\n```", f"Here it is: {text} done."):
        first, _ = _answers(wrapped, items)
        assert first["over"] == Answer(True), wrapped
    (inner,) = _answers('{"0": {"answers": {"over": true, "over": false}}}', items[:1])
    assert inner["over"] == Answer(True)
    assert decisions.find_object('{"a": 1, "a": 2}') == {"a": 1}
    assert decisions.find_object('```json\n{"a": 1, "a": 2}\n```') == {"a": 1}
    assert decisions.find_object('see {"a": 1, "a": 2} ok') == {"a": 1}


def test_was_read():
    assert decisions.was_read(Answer(True))
    assert decisions.was_read(Answer(0))
    assert decisions.was_read(Answer(None, "unreadable"))
    assert decisions.was_read(Answer(None, "unreadable", detail=decisions.NOT_AN_OPTION))
    assert not decisions.was_read(Answer(None, "unreadable", detail=decisions.NO_OBJECT))
    assert not decisions.was_read(Answer(None, "unreadable", detail=decisions.NO_ITEM))
    for reason in ("error", "refused", "abstained"):
        assert not decisions.was_read(Answer(None, reason))


def test_held_no_object():
    assert decisions.held_no_object(Answer(None, "unreadable", detail=decisions.NO_OBJECT))
    assert not decisions.held_no_object(Answer(None, "unreadable", detail=decisions.NO_ITEM))
    assert not decisions.held_no_object(Answer(None, "unreadable"))
    assert not decisions.held_no_object(Answer(None, "error"))
    assert not decisions.held_no_object(Answer("yes"))
    assert not decisions.held_no_object(None)


def test_offerable_is_what_validate_refuses():
    assert not decisions.offerable("")
    assert not decisions.offerable("   ")
    assert decisions.offerable("the-map")
    for bad in ("", "   "):
        with pytest.raises(DecideRequestError):
            decisions.validate([_choice_item((Option(bad, ""), MARA))])
        with pytest.raises(DecideRequestError):
            decisions.validate([_choice_item((Option("the-map", "", (bad,)), MARA))])


def test_explicit_none_is_abstained():
    (answers,) = _answers('{"0": {"answers": {"over": true, "who": null, "tone": 1}}}',
                          [_item(allow_none=True)])
    assert answers["who"] == Answer(None, "abstained")


def test_parse_never_raises():
    items = [_item(), _item()]
    for text in ("", "{", "null", '{"0": 5}', '{"0": {"answers": []}}', "```", "}{",
                 '{"0": {"answers": {"who": {"x": 1}, "tone": [1]}}}', "\x00", "1e999"):
        out = decisions.parse(text, items, explain=True)
        assert len(out) == len(items), text
        assert all(isinstance(r, ItemResult) for r in out)


def test_rationale_only_when_explained_and_a_string():
    item = Item("ctx", (Predicate("over", "i"),))

    def rationale(value, *, explain):
        text = json.dumps({"0": {"answers": {"over": True}, "rationale": value}})
        (result,) = decisions.parse(text, [item], explain=explain)
        return result.rationale

    assert rationale("  because  ", explain=True) == "because"
    assert rationale("because", explain=False) == ""
    assert rationale(5, explain=True) == ""
    assert rationale(None, explain=True) == ""
    assert rationale(["because"], explain=True) == ""


# --- answers, chunks, unanswered --------------------------------------------

def test_answer_holds_its_invariants():
    Answer(False)
    Answer(0)
    Answer(None, "refused")
    Answer(None, "unreadable", detail=decisions.NOT_AN_OPTION)
    for bad in (
        lambda: Answer(None),                                  # None with no reason
        lambda: Answer(True, "unreadable"),                    # reason on an answer
        lambda: Answer(None, "guessed"),                       # outside REASONS
        lambda: Answer(None, "abstained", detail="not_an_option"),  # detail off unreadable
    ):
        with pytest.raises(ValueError):
            bad()


def test_decision_backend_is_one_of_backends_or_none_on_a_mixed_batch():
    for backend in decisions.BACKENDS:
        assert decisions.Decision((), backend).backend == backend
    # Stages with different backends answered the batch (slice H): no one
    # backend did, and each ItemResult says which answered it.
    assert decisions.Decision((), "").backend == ""
    for bad in ("Structured", "openrouter"):
        with pytest.raises(ValueError):
            decisions.Decision((), bad)


def test_parse_reads_a_list_holding_one_object():
    (result,) = decisions.parse(f"[{REPLY}]", [_item()], explain=True)
    assert result == decisions.parse(REPLY, [_item()], explain=True)[0]


def test_parse_a_list_holding_two_objects_is_unreadable():
    for result in decisions.parse(f"[{REPLY}, {REPLY}]", [_item()], explain=True):
        assert result.rationale == ""
        assert all(_unreadable(a, decisions.NO_OBJECT) for a in result.answers.values())


def test_validate_refuses_whitespace_only_ids():
    with pytest.raises(DecideRequestError):
        decisions.validate([Item("ctx", (Predicate("  ", "i"),))])
    with pytest.raises(DecideRequestError):
        decisions.validate([_choice_item((Option(" \t ", ""), MARA))])


def test_parse_of_none_is_unreadable():
    (result,) = decisions.parse(None, [_item()], explain=True)  # type: ignore[arg-type]
    assert result == ItemResult({q.id: Answer(None, "unreadable", detail=decisions.NO_OBJECT)
                                 for q in _item().questions})


def test_chunks_refuses_an_empty_size():
    items = [Item("a", (Predicate("p", "i"),))]
    for size in (0, -1):
        with pytest.raises(ValueError):
            decisions.chunks(items, size)


def test_chunks():
    items = [Item(str(i), (Predicate("p", "i"),)) for i in range(17)]
    out = decisions.chunks(items, 8)
    assert [offset for offset, _ in out] == [0, 8, 16]
    assert [len(chunk) for _, chunk in out] == [8, 8, 1]
    assert [i for _, chunk in out for i in chunk] == items
    assert all(isinstance(chunk, tuple) for _, chunk in out)
    assert decisions.chunks(items) == out
    assert decisions.chunks([]) == []


def test_unanswered():
    items = [_item(), Item("b", (Predicate("p", "i"),))]
    assert decisions.unanswered(items, "error") == (
        ItemResult({"over": Answer(None, "error"), "who": Answer(None, "error"),
                    "tone": Answer(None, "error")}),
        ItemResult({"p": Answer(None, "error")}),
    )


def test_constants():
    assert decisions.REASONS == ("unreadable", "refused", "abstained", "error")
    assert decisions.BACKENDS == ("structured", "native")
    assert decisions.NOT_AN_OPTION == "not_an_option"
    assert decisions.NO_OBJECT == "no_object"
    assert decisions.NO_ITEM == "no_item"
    assert decisions.MIN_OPTIONS_WITH_NONE == 1
    assert (decisions.MIN_OPTIONS, decisions.MAX_OPTIONS) == (2, 255)
    assert (decisions.MIN_LEVELS, decisions.MAX_LEVELS) == (2, 10)
    assert decisions.MAX_ITEMS_PER_CALL == 8
    assert decisions.RESERVED_IDS == ("answers", "rationale")


def test_decisions_is_a_leaf():
    assert _sibling_imports("decisions") == set()


# --- native: reserved none, option limit, string budgets ---------------------

ONE_NULLABLE = Choice("id", "i", (Option("find-the-ledger", "Find the ledger"),),
                      allow_none=True)


def _long_ids(n: int, width: int, prefix: str = "o") -> tuple[Option, ...]:
    return tuple(Option(f"{prefix}{i:04d}".ljust(width, "x"), "") for i in range(n))


def _predicates(n: int, width: int = 0) -> Item:
    return Item("ctx", tuple(Predicate(f"q{i:04d}".ljust(width, "x"), "i")
                             for i in range(n)))


def test_item_result_backend_defaults_empty_and_parse_leaves_it():
    assert ItemResult({}).backend == ""
    for backend in decisions.BACKENDS:
        assert ItemResult({}, backend=backend).backend == backend
    with pytest.raises(ValueError):
        ItemResult({}, backend="openrouter")
    for result in decisions.parse(REPLY, [_item()], explain=True):
        assert result.backend == ""
    for result in decisions.unanswered([_item()], "error"):
        assert result.backend == ""


def test_none_key_is_refused_as_an_option():
    assert decisions.NONE_KEY == "<none>"
    with pytest.raises(DecideRequestError):
        decisions.validate([_choice_item((Option("<none>", ""), MARA))])
    with pytest.raises(DecideRequestError):
        decisions.validate([_choice_item((Option("the-map", "", ("<NONE>",)), MARA))])


def test_none_key_is_not_offerable():
    assert not decisions.offerable("<none>")
    assert not decisions.offerable("<NONE>")
    assert not decisions.offerable("  <none> ")
    assert decisions.offerable("the-map")
    assert decisions.offerable("none")


def test_native_choice_keys_add_one_reserved_none():
    plain = Choice("who", "i", (MARA, GRIMOIRE))
    assert decisions.native_choice_keys(plain) == (
        ("characters:mara", "characters:mara"), ("grimoire", "grimoire"))
    nullable = Choice("who", "i", (MARA, GRIMOIRE), allow_none=True)
    assert decisions.native_choice_keys(nullable) == (
        ("characters:mara", "characters:mara"), ("grimoire", "grimoire"),
        ("none", decisions.NONE_KEY))
    assert decisions.NATIVE_NONE == "none"
    assert decisions.NATIVE_NONE_TEXT == "None of the other options fits."

    taken = Choice("who", "i", (Option("none", ""), MARA), allow_none=True)
    assert decisions.native_choice_keys(taken)[-1] == ("none_2", decisions.NONE_KEY)
    both = Choice("who", "i", (Option("none", ""), Option("none_2", ""), MARA),
                  allow_none=True)
    assert decisions.native_choice_keys(both)[-1] == ("none_3", decisions.NONE_KEY)
    assert decisions.native_choice_keys(ONE_NULLABLE) == (
        ("find-the-ledger", "find-the-ledger"), ("none", decisions.NONE_KEY))


def test_native_gap_names_a_nullable_choice_past_the_option_limit():
    assert decisions.NATIVE_MAX_OPTIONS == 255
    past = _choice_item(_options(255), allow_none=True)
    decisions.validate([past])
    assert decisions.native_gap(past) == (
        "Question q offers 256 options including none; "
        "a decisions endpoint takes at most 255.")
    for fits in (_choice_item(_options(254), allow_none=True),
                 _choice_item(_options(255)),
                 _choice_item(_options(1), allow_none=True),
                 _item()):
        assert decisions.native_gap(fits) == ""


def test_validate_refuses_an_enum_past_the_string_budget():
    assert decisions.MAX_ENUM_STRING_CHARS == 15_000
    assert decisions.ENUM_STRING_CHARS_ABOVE == 250
    with pytest.raises(DecideRequestError, match=r"whose ids total 15060 characters"):
        decisions.validate([_choice_item(_long_ids(251, 60))])
    decisions.validate([_choice_item(_long_ids(251, 50))])  # 12,550 characters
    decisions.validate([_choice_item(_long_ids(250, 70))])  # 250 values: no per-enum rule


def test_validate_refuses_an_item_past_the_schema_string_budget():
    assert decisions.MAX_SCHEMA_STRING_CHARS == 120_000
    decisions.validate([_predicates(470, 250)])  # 117,500 + the item's own keys
    with pytest.raises(DecideRequestError, match=r"schema holds 125017 characters"):
        decisions.validate([_predicates(500, 250)])  # 125,000
    # Enum strings count too: four choices of 250 values (1,000, inside the
    # enum budget, and none above 250) of 125 characters each.
    wide = Item("ctx", tuple(Choice(f"c{n}", "i", _long_ids(250, 125, f"c{n}-"))
                             for n in range(4)))
    assert decisions.enum_values(wide) == decisions.MAX_ENUM_VALUES
    with pytest.raises(DecideRequestError, match=r"schema holds 125025 characters"):
        decisions.validate([wide])


def test_validate_refuses_an_item_past_the_schema_property_budget():
    assert decisions.MAX_SCHEMA_PROPERTIES == 5000
    # Its own index, `answers`, `rationale`, and one property per question.
    assert decisions.schema_properties([_predicates(4997)]) == 5000
    decisions.validate([_predicates(4997)])
    with pytest.raises(DecideRequestError, match=r"schema holds 5001 properties"):
        decisions.validate([_predicates(4998)])


def test_chunks_close_before_the_schema_string_budget():
    fifty, seventy = _predicates(200, 250), _predicates(280, 250)
    assert decisions.schema_chars([fifty]) == 1 + 7 + 9 + 50_000
    items = [fifty, seventy, fifty, fifty, fifty]
    out = decisions.chunks(items)
    assert [(offset, len(chunk)) for offset, chunk in out] == [(0, 1), (1, 1), (2, 2), (4, 1)]
    assert [i for _, chunk in out for i in chunk] == items
    for _, chunk in out:
        assert decisions.schema_chars(chunk) <= decisions.MAX_SCHEMA_STRING_CHARS
    # An item that alone exceeds the budget is a chunk of its own.
    alone = _predicates(500, 250)
    assert [len(c) for _, c in decisions.chunks([fifty, alone, fifty])] == [1, 1, 1]
    assert [len(c) for _, c in decisions.chunks([fifty] * 3, chars=10**9)] == [3]


def test_chunks_close_before_the_schema_property_budget():
    items = [_predicates(2000)] * 5
    out = decisions.chunks(items)
    assert [len(chunk) for _, chunk in out] == [2, 2, 1]
    for _, chunk in out:
        assert decisions.schema_properties(chunk) <= decisions.MAX_SCHEMA_PROPERTIES


def test_schema_chars_counts_keys_and_enum_strings():
    expected = (len("0") + len("answers") + len("rationale") + len("over") + len("who")
                + len("tone") + len("characters:mara") + len("grimoire"))
    assert decisions.schema_chars([_item()]) == expected
    assert decisions.schema_chars([_item(allow_none=False)]) == expected
    # Integer enums do not count; a second item adds its index and its keys.
    two = decisions.schema_chars([_item(), Item("b", (Predicate("p", "i"),))])
    assert two == expected + len("1") + len("answers") + len("rationale") + len("p")
    assert decisions.schema_properties([_item()]) == 1 + 2 + 3


# --- native: answers ---------------------------------------------------------

PRED = Predicate("over", "i")
WHO = Choice("who", "i", (MARA, GRIMOIRE), allow_none=True)
STRICT_WHO = Choice("who", "i", (MARA, GRIMOIRE))
TONE = Score("tone", "i", ("low", "mid", "high"))
NONE_KEY = "<none>"  # decisions.NONE_KEY, spelled out


def _native(q, **kw):
    return decisions.native_answer(q, **kw)


def test_native_answer_predicate():
    assert _native(PRED, chosen=True) == Answer(True)
    assert _native(PRED, chosen=False) == Answer(False)
    assert _native(PRED, probability=0.7) == Answer(True, probability=0.7)
    assert _native(PRED, probability=0.3) == Answer(False, probability=0.3)
    assert _native(PRED, probability=1) == Answer(True, probability=1.0)
    assert _native(PRED, probability=0.5) == Answer(None, "abstained", probability=0.5)
    for bad in (1.5, -0.1, float("nan"), float("inf"), True, "0.7", None, 10**400,
                -(10**400)):
        assert _native(PRED, probability=bad) == Answer(None, "unreadable"), bad
    # An int too large for a float is out of range, never an OverflowError.
    assert _native(PRED, chosen=True, probability=10**400) == Answer(True)
    assert _native(PRED) == Answer(None, "unreadable")
    # A non-bool `chosen` is not a predicate's answer; the probability decides.
    assert _native(PRED, chosen="yes", probability=0.8) == Answer(True, probability=0.8)


def test_native_answer_explicit_predicate_wins_over_its_probability():
    """Deliberate (M11): a provider's explicit boolean is its answer, and the
    probability it reported beside it is carried as reported. Neither is
    reconciled into the other -- do not "fix" this into a threshold on the
    probability, which would overrule an answer the provider gave."""
    assert _native(PRED, chosen=True, probability=0.2) == Answer(True, probability=0.2)
    assert _native(PRED, chosen=False, probability=0.9) == Answer(False, probability=0.9)


def test_native_answer_choice():
    assert _native(WHO, chosen="characters:mara") == Answer("characters:mara")
    assert _native(WHO, chosen="Characters:Mara") == Answer(
        None, "unreadable", detail=decisions.NOT_AN_OPTION)
    assert _native(WHO, chosen=5) == Answer(None, "unreadable", detail=decisions.NOT_AN_OPTION)
    for nothing in (NONE_KEY, None):
        assert _native(WHO, chosen=nothing) == Answer(None, "abstained")
        assert _native(STRICT_WHO, chosen=nothing) == Answer(None, "unreadable")

    dist = {"characters:mara": 0.6, "grimoire": 0.4}
    assert _native(WHO, distribution=dist) == Answer("characters:mara", distribution=dist)
    on_none = {"characters:mara": 0.2, "grimoire": 0.1, NONE_KEY: 0.7}
    assert _native(WHO, distribution=on_none) == Answer(
        None, "abstained", distribution=on_none)
    tie = {"characters:mara": 0.5, "grimoire": 0.5}
    assert _native(WHO, distribution=tie) == Answer(None, "abstained", distribution=tie)
    assert _native(WHO) == Answer(None, "unreadable")
    assert _native(WHO, distribution={}) == Answer(None, "unreadable")

    stray = {"characters:rowan": 0.9, "grimoire": 0.1}
    assert _native(WHO, chosen="grimoire", distribution=stray) == Answer("grimoire")
    assert _native(WHO, distribution=stray) == Answer(None, "unreadable")
    # The reserved none is a legal key only where none is allowed.
    assert _native(STRICT_WHO, distribution=on_none) == Answer(None, "unreadable")
    for bad in ({"grimoire": 1.2}, {"grimoire": float("nan")}, {"grimoire": True},
                {"grimoire": "0.4"}, [("grimoire", 0.4)], {0: 0.4},
                {"grimoire": 10**400}, {"characters:mara": 0.6, "grimoire": -(10**400)}):
        assert _native(WHO, distribution=bad) == Answer(None, "unreadable"), bad
    # The overflowing report is dropped whole; an explicit answer still stands.
    assert _native(WHO, chosen="grimoire", distribution={"grimoire": 10**400}) == (
        Answer("grimoire"))
    assert _native(TONE, distribution={"0": 0.1, "2": 10**400}) == Answer(None, "unreadable")
    # The explicit answer wins over its distribution, which rides along.
    assert _native(WHO, chosen="grimoire", distribution=dist) == Answer(
        "grimoire", distribution=dist)


def test_native_answer_one_option_nullable_choice():
    assert _native(ONE_NULLABLE, chosen="find-the-ledger") == Answer("find-the-ledger")
    assert _native(ONE_NULLABLE, chosen=NONE_KEY) == Answer(None, "abstained")
    assert _native(ONE_NULLABLE, distribution={"find-the-ledger": 0.3, NONE_KEY: 0.7}) == (
        Answer(None, "abstained", distribution={"find-the-ledger": 0.3, NONE_KEY: 0.7}))


def test_native_answer_score():
    assert _native(TONE, chosen=2) == Answer(2)
    assert _native(TONE, chosen=3) == Answer(None, "unreadable")
    assert _native(TONE, chosen=-1) == Answer(None, "unreadable")
    assert _native(TONE, chosen=True) == Answer(None, "unreadable")
    dist = {"0": 0.1, "1": 0.2, "2": 0.7}
    assert _without_expected(_native(TONE, distribution=dist)) == Answer(2, distribution=dist)
    assert _native(TONE, distribution={"0": 0.1, "3": 0.9}) == Answer(None, "unreadable")


def test_native_answer_score_ignores_a_fractional_weighted_score():
    """I1: both providers return a probability-weighted, fractional `score`.
    It is never rounded into an answer: the answer is the argmax of the
    per-level probabilities, or nothing."""
    dist = {"0": 0.1, "1": 0.2, "2": 0.7}
    assert _without_expected(_native(TONE, chosen=1.4, distribution=dist)) == Answer(
        2, distribution=dist)
    assert _native(TONE, chosen=1.4) == Answer(None, "unreadable")
    bimodal = {"0": 0.45, "1": 0.1, "2": 0.45}
    assert _without_expected(_native(TONE, chosen=1.0, distribution=bimodal)) == Answer(
        None, "abstained", distribution=bimodal)


def _without_expected(answer: Answer) -> Answer:
    return dataclasses.replace(answer, expected=None)


def test_native_score_expected_is_the_mass_normalised_weighted_level():
    """01e-C2: `expected` is computed over the levels a report names, divided
    by the mass it reported -- an omitted level is unreported, not zero."""
    assert _native(TONE, distribution={"0": 0.1, "1": 0.2, "2": 0.7}).expected == (
        pytest.approx(1.6))
    # Two levels reported, summing to 0.5: normalised, not read as 0.5 of a level.
    assert _native(TONE, distribution={"1": 0.25, "2": 0.25}).expected == pytest.approx(1.5)
    # A tie at the top is abstained, and the expected level still rides.
    tied = _native(TONE, distribution={"0": 0.1, "1": 0.45, "2": 0.45})
    assert (tied.answer, tied.reason) == (None, "abstained")
    assert tied.expected == pytest.approx(1.35)
    # A chosen level keeps the expected level of the distribution beside it.
    assert _native(TONE, chosen=0, distribution={"0": 0.5, "2": 0.5}).expected == (
        pytest.approx(1.0))


def test_native_score_expected_absent_without_a_usable_distribution():
    assert _native(TONE, chosen=2).expected is None
    assert _native(TONE, distribution={"0": 0.0, "1": 0.0}).expected is None
    assert _native(TONE, distribution={"0": 0.1, "3": 0.9}).expected is None
    # Never read off a provider's weighted score, which is not handed over.
    assert _native(TONE, chosen=1.4).expected is None
    assert _native(TONE, refused=True).expected is None


def test_expected_and_marginals_never_ride_another_question():
    dist = {"characters:mara": 0.6, "grimoire": 0.4}
    assert _native(WHO, distribution=dist).expected is None
    assert _native(PRED, probability=0.8).expected is None
    # Marginals come only from a lowered rank or selection, never from a
    # question a native endpoint answers as itself.
    assert _native(PRED, probability=0.8).marginals is None
    assert _native(WHO, distribution=dist).marginals is None
    assert _native(TONE, distribution={"0": 0.5, "2": 0.5}).marginals is None


def test_a_structured_answer_carries_no_expected_or_marginals():
    item = Item("ctx", (Score("tone", "i", ("low", "mid", "high")),))
    (result,) = decisions.parse('{"0": {"answers": {"tone": 1}}}', [item], explain=False)
    assert result.answers["tone"] == Answer(1)
    assert result.answers["tone"].expected is None
    assert result.answers["tone"].marginals is None


def test_answer_checks_its_marginals_and_expected():
    Answer(None, "unreadable", marginals={"a": 0.0, "b": 1.0})
    Answer(2, expected=1.5)
    for bad in ({"a": 1.5}, {"a": -0.1}, {"a": True}, {"a": float("nan")}, {1: 0.5}, [1],
                {"a": 10**400}):
        with pytest.raises(ValueError):
            Answer(None, "unreadable", marginals=bad)
    for bad in (float("inf"), float("nan"), True, "1.5"):
        with pytest.raises(ValueError):
            Answer(1, expected=bad)
    Answer(1, expected=10**400)   # an int is finite however large


# --- tiers --------------------------------------------------------------------

def test_tiers_groups_exact_ties_and_keeps_the_mapping_order():
    assert decisions.tiers({"a": 0.2, "b": 0.9, "c": 0.2, "d": 0.5}) == (
        ("b",), ("d",), ("a", "c"))
    assert decisions.tiers({}) == ()


def test_tiers_ties_values_within_mass_tie():
    assert decisions.tiers({"a": 0.1 + 0.2, "b": 0.3, "c": 0.1}) == (("a", "b"), ("c",))
    near = decisions.MASS_TIE / 2
    assert decisions.tiers({"x": 0.5, "y": 0.5 - near}) == (("x", "y"),)
    assert decisions.tiers({"x": 0.5, "y": 0.5 - 3 * decisions.MASS_TIE}) == (("x",), ("y",))


def test_tiers_anchor_on_the_tiers_highest_value():
    """Three values each just inside tolerance of the next do not chain into
    one tier: each joins only when within tolerance of its tier's first."""
    step = 0.6 * decisions.MASS_TIE
    values = {"a": 0.5, "b": 0.5 - step, "c": 0.5 - 2 * step, "d": 0.5 - 3 * step}
    assert decisions.tiers(values) == (("a", "b"), ("c", "d"))


def test_tiers_of_integer_levels():
    assert decisions.tiers({"s1": 7, "s2": 3, "s3": 7, "s4": 9}) == (
        ("s4",), ("s1", "s3"), ("s2",))
    assert decisions.tiers({"s1": 2, "s2": 1}, tolerance=1) == (("s1", "s2"),)


def test_tiers_refuses_a_value_with_no_place_in_an_order():
    for bad in (float("nan"), float("inf"), True, None, "0.5"):
        with pytest.raises(ValueError):
            decisions.tiers({"a": 0.5, "b": bad})
    for tolerance in (-1, True, float("nan")):
        with pytest.raises(ValueError):
            decisions.tiers({"a": 0.5}, tolerance=tolerance)
    # An int too large for a float still orders, rather than overflowing.
    assert decisions.tiers({"a": 10**400, "b": 1}) == (("a",), ("b",))


def test_native_answer_refused():
    for q in (PRED, WHO, TONE):
        assert _native(q, refused=True) == Answer(None, "refused")
        assert _native(q, refused=True, chosen=True, probability=0.9) == Answer(None, "refused")


def test_native_answer_outcomes_against_was_read():
    assert not decisions.was_read(_native(WHO, refused=True))
    assert not decisions.was_read(_native(WHO, distribution={"characters:mara": 0.5,
                                                             "grimoire": 0.5}))
    assert not decisions.was_read(_native(WHO, chosen=NONE_KEY))
    unread_none = _native(STRICT_WHO, chosen=NONE_KEY)
    assert unread_none == Answer(None, "unreadable")
    assert decisions.was_read(unread_none)
    assert decisions.was_read(_native(WHO, chosen="grimoire"))


# --- native: the capture's outcome, and the structured rendering -------------

def test_outcome_omits_empty_fields():
    dist = {"characters:mara": 0.2, "grimoire": 0.1, NONE_KEY: 0.7}
    results = (
        ItemResult({"over": Answer(False, probability=0.1),
                    "who": Answer(None, "abstained", distribution=dist),
                    "tone": Answer(0)}, backend="native"),
        ItemResult({"over": Answer(True),
                    "who": Answer(None, "unreadable", detail=decisions.NOT_AN_OPTION),
                    "tone": Answer(None, "unreadable")}, "Mara spoke last.",
                   backend="structured"),
        ItemResult({"over": Answer(None, "error")}),
    )
    assert decisions.outcome("native", "openrouter", "vendor/model", results) == {
        "mode": "native", "provider": "openrouter", "model": "vendor/model",
        "items": [
            {"backend": "native", "answers": {
                "over": {"answer": False, "probability": 0.1},
                "who": {"answer": None, "reason": "abstained", "distribution": dist},
                "tone": {"answer": 0}}},
            {"backend": "structured", "answers": {
                "over": {"answer": True},
                "who": {"answer": None, "reason": "unreadable", "detail": "not_an_option"},
                "tone": {"answer": None, "reason": "unreadable"}},
             "rationale": "Mara spoke last."},
            {"answers": {"over": {"answer": None, "reason": "error"}}},
        ]}
    assert decisions.outcome("structured", "", "", ()) == {"mode": "structured", "items": []}
    json.dumps(decisions.outcome("native", "openrouter", "vendor/model", results))


def test_outcome_writes_marginals_and_expected_when_present():
    results = (ItemResult({
        "tone": Answer(None, "abstained", distribution={"1": 0.5, "2": 0.5}, expected=1.5),
        "rank": Answer(None, "unreadable", marginals={"scene:a": 0.8, "scene:b": 0.3}),
        "plain": Answer(True, marginals={}, expected=None)}, backend="native"),)
    assert decisions.outcome("native", "openai", "m", results)["items"] == [
        {"backend": "native", "answers": {
            "tone": {"answer": None, "reason": "abstained",
                     "distribution": {"1": 0.5, "2": 0.5}, "expected": 1.5},
            "rank": {"answer": None, "reason": "unreadable",
                     "marginals": {"scene:a": 0.8, "scene:b": 0.3}},
            "plain": {"answer": True}}}]
    # An expected level of exactly 0 is a value, not an empty field.
    zero = (ItemResult({"tone": Answer(0, distribution={"0": 1.0}, expected=0.0)}),)
    assert decisions.outcome("native", "", "", zero)["items"][0]["answers"]["tone"][
        "expected"] == 0.0


def test_outcome_of_a_failure():
    assert decisions.outcome("native", "openrouter", "vendor/model",
                             error="bad_response") == {
        "mode": "native", "provider": "openrouter", "model": "vendor/model",
        "error": "bad_response"}


def test_render_round_trips_answered_values():
    items = [_item(), _item(allow_none=False), Item("b", (Predicate("p", "i"),))]
    results = (
        ItemResult({"over": Answer(True), "who": Answer("characters:mara"),
                    "tone": Answer(2)}, "Because.", backend="native"),
        ItemResult({"over": Answer(False), "who": Answer("grimoire"), "tone": Answer(0)},
                   backend="native"),
        ItemResult({"p": Answer(None, "abstained")}, backend="native"),
    )
    for explain in (True, False):
        text = decisions.render(results, items, explain=explain)
        back = decisions.parse(text, items, explain=explain)
        assert [{k: a.answer for k, a in r.answers.items()} for r in back] == [
            {k: a.answer for k, a in r.answers.items()} for r in results]
        assert [r.rationale for r in back] == (
            [r.rationale for r in results] if explain else ["", "", ""])
    assert json.loads(decisions.render(results[:1], items[:1], explain=True)) == {
        "0": {"answers": {"over": True, "who": "characters:mara", "tone": 2},
              "rationale": "Because."}}
    nullable = decisions.render(
        (ItemResult({"over": Answer(True), "who": Answer(None, "abstained"),
                     "tone": Answer(1)}),), items[:1], explain=False)
    assert json.loads(nullable) == {"0": {"answers": {"over": True, "who": None, "tone": 1}}}
    (back,) = decisions.parse(nullable, items[:1], explain=False)
    assert back.answers["who"] == Answer(None, "abstained")


# --- 01e: Rank ----------------------------------------------------------------


PIER = Option("scenes:pier", "The debt at the pier", aliases=("the pier",))
LEDGER = Option("scenes:ledger", "The ledger changes hands")
TIDE = Option("scenes:tide", "The tide comes in")


def _rank(**kw) -> Rank:
    return Rank("order", "Which scene matters most?", (PIER, LEDGER, TIDE), **kw)


def _rank_item(**kw) -> Item:
    return Item("ctx", (_rank(**kw),))


def _read_rank(value, **kw) -> Answer:
    item = _rank_item(**kw)
    (result,) = decisions.parse(json.dumps({"0": {"answers": {"order": value}}}), [item],
                                explain=False)
    return result.answers["order"]


def test_question_kinds_are_class_constants():
    assert [q.KIND for q in (PRED, WHO, TONE, _rank())] == [
        "predicate", "choice", "score", "rank"]
    # Neither a constructor field nor an equality field.
    assert "KIND" not in {f.name for f in dataclasses.fields(Rank)}
    assert _rank() == _rank()
    assert decisions.kinds([_item(), _rank_item()]) == {"predicate", "choice", "score", "rank"}


def test_validate_rank_bounds():
    decisions.validate([_rank_item()])
    decisions.validate([Item("c", (Rank("r", "i", _options(decisions.MAX_RANK_CANDIDATES)),))])
    for n in (0, 1, decisions.MAX_RANK_CANDIDATES + 1):
        with pytest.raises(DecideRequestError, match="candidates"):
            decisions.validate([Item("c", (Rank("r", "i", _options(n)),))])
    for top in (1, 3):
        decisions.validate([_rank_item(top=top)])
    for top in (0, 4, -1, True, 1.5):
        with pytest.raises(DecideRequestError, match="top"):
            decisions.validate([_rank_item(top=top)])


def test_validate_rank_candidates_follow_the_choice_rules():
    with pytest.raises(DecideRequestError, match="collides"):
        decisions.validate([Item("c", (Rank("r", "i", (PIER, Option("Scenes:Pier", ""))),))])
    with pytest.raises(DecideRequestError, match="collides"):
        decisions.validate([Item("c", (Rank("r", "i", (PIER, Option("the_pier", ""))),))])
    with pytest.raises(DecideRequestError, match="reserved"):
        decisions.validate([Item("c", (Rank("r", "i", (PIER, Option(NONE_KEY, ""))),))])
    with pytest.raises(DecideRequestError, match="not an Option"):
        decisions.validate([Item("c", (Rank("r", "i", (PIER, "scenes:tide")),))])


def test_rank_schema_is_an_array_of_an_enum():
    ids = ["scenes:pier", "scenes:ledger", "scenes:tide"]
    listed = {"type": "array", "items": {"type": "string", "enum": ids}}
    answers = decisions.schema([_rank_item()], explain=False)["properties"]["0"][
        "properties"]["answers"]["properties"]
    assert answers["order"] == listed
    nullable = decisions.schema([_rank_item(allow_none=True)], explain=False)["properties"][
        "0"]["properties"]["answers"]["properties"]["order"]
    assert nullable == {"anyOf": [listed, {"type": "null"}]}
    text = json.dumps(decisions.schema([_rank_item(top=1, allow_none=True)], explain=True))
    for keyword in ("minItems", "maxItems", "uniqueItems"):
        assert keyword not in text


def test_rank_counts_its_enum_values_and_strings():
    assert decisions.enum_values(_rank_item()) == 3
    assert decisions.enum_values(Item("c", (PRED, _rank(), TONE))) == 6
    base = decisions.schema_chars([Item("c", (PRED,))])
    with_rank = decisions.schema_chars([Item("c", (PRED, _rank()))])
    assert with_rank - base == len("order") + sum(len(o.id) for o in (PIER, LEDGER, TIDE))


def test_parse_rank_exact_aliased_and_normalised():
    answer = _read_rank(["scenes:ledger", "The Pier", "SCENES:TIDE"])
    assert answer == Answer(Ranking(tiers=(("scenes:ledger",), ("scenes:pier",),
                                           ("scenes:tide",))))
    assert answer.distribution is None and answer.marginals is None and answer.expected is None


def test_parse_rank_unknown_entry_is_not_an_option_and_not_dropped():
    answer = _read_rank(["scenes:ledger", "scenes:harbour", "scenes:pier", "scenes:tide"])
    assert _unreadable(answer, decisions.NOT_AN_OPTION)
    assert answer.stated == ""
    assert decisions.was_read(answer)
    assert _unreadable(_read_rank(["scenes:ledger", 3, "scenes:pier", "scenes:tide"]),
                       decisions.NOT_AN_OPTION)


def test_parse_rank_duplicate_is_unreadable_and_read():
    answer = _read_rank(["scenes:ledger", "the pier", "scenes:pier", "scenes:tide"])
    assert _unreadable(answer)
    assert decisions.was_read(answer)


def test_parse_rank_too_short_is_unreadable():
    assert _unreadable(_read_rank(["scenes:ledger", "scenes:pier"]))
    assert _unreadable(_read_rank([]))
    assert _unreadable(_read_rank(["scenes:ledger"], top=2))


def test_parse_rank_top_leaves_the_rest_unranked_in_input_order():
    assert _read_rank(["scenes:tide"], top=1) == Answer(
        Ranking(tiers=(("scenes:tide",),), rest=("scenes:pier", "scenes:ledger")))
    # Ranking past `top` is allowed; the rest shrinks.
    assert _read_rank(["scenes:tide", "scenes:pier"], top=1) == Answer(
        Ranking(tiers=(("scenes:tide",), ("scenes:pier",)), rest=("scenes:ledger",)))


def test_parse_rank_null_and_wrong_types():
    assert _read_rank(None, allow_none=True) == Answer(None, "abstained")
    assert _unreadable(_read_rank(None))
    for value in ("scenes:pier", {"0": "scenes:pier"}, True, 2):
        assert _unreadable(_read_rank(value))


def test_ranking_flat_never_breaks_a_tie_silently():
    strict = Ranking(tiers=(("a",), ("b",)), rest=("c", "d"))
    assert strict.flat() == ("a", "b")
    with pytest.raises(ValueError, match="tie-break"):
        strict.flat(rest=True)
    assert strict.flat(lambda c: c, rest=True) == ("a", "b", "c", "d")
    tied = Ranking(tiers=(("a",), ("c", "b")))
    with pytest.raises(ValueError, match="tie-break"):
        tied.flat()
    assert tied.flat(lambda c: c) == ("a", "b", "c")
    assert tied.flat({"c": 0, "b": 1}.__getitem__) == ("a", "c", "b")


def test_ranking_position_and_validation():
    ranking = Ranking(tiers=(("a",), ("c", "b")), rest=("d",))
    assert [ranking.position(c) for c in "abcde"] == [0, 1, 1, None, None]
    with pytest.raises(ValueError):
        Ranking(tiers=(("a",), ("a",)))
    with pytest.raises(ValueError):
        Ranking(tiers=(("a",),), rest=("a",))
    with pytest.raises(ValueError):
        Ranking(tiers=((),))


def test_outcome_and_render_spell_a_ranking():
    items = [_rank_item(top=1)]
    strict = Ranking(tiers=(("scenes:tide",), ("scenes:pier",)), rest=("scenes:ledger",))
    results = (ItemResult({"order": Answer(strict)}, backend="structured"),)
    assert decisions.outcome("structured", "p", "m", results)["items"][0]["answers"] == {
        "order": {"answer": {"tiers": [["scenes:tide"], ["scenes:pier"]],
                             "rest": ["scenes:ledger"]}}}
    text = decisions.render(results, items, explain=False)
    assert json.loads(text) == {"0": {"answers": {"order": ["scenes:tide", "scenes:pier"]}}}
    (back,) = decisions.parse(text, items, explain=False)
    assert back.answers["order"] == Answer(strict)
    tied = (ItemResult({"order": Answer(Ranking(tiers=(("scenes:tide", "scenes:pier"),),
                                                rest=("scenes:ledger",)),
                                        marginals={"scenes:tide": 0.7, "scenes:pier": 0.7,
                                                   "scenes:ledger": 0.1})}, backend="native"),)
    assert json.loads(decisions.render(tied, items, explain=False)) == {
        "0": {"answers": {"order": None}}}
    json.dumps(decisions.outcome("native", "p", "m", tied))


def test_chunks_count_a_ranks_candidates():
    big = Item("c", (Rank("r", "i", _options(decisions.MAX_RANK_CANDIDATES)),))
    assert [len(c) for _, c in decisions.chunks([big] * 9)] == [8, 1]
    assert [len(c) for _, c in decisions.chunks([big] * 4, budget=100)] == [3, 1]


def test_native_gap_refuses_only_a_rank_with_no_pointwise_question():
    gap = decisions.native_gap(Item("c", (PRED, _rank())))
    assert "order" in gap and "pointwise" in gap
    assert decisions.native_gap(Item("c", (PRED, _rank(pointwise="Does it bear on this?")))) == ""
    # Whitespace is no question: it would lower into blank predicates.
    assert "pointwise" in decisions.native_gap(Item("c", (_rank(pointwise=" \n "),)))
    assert decisions.native_gap(Item("c", (PRED, TONE))) == ""


def test_native_gap_names_a_lowered_id_collision():
    rank = _rank(pointwise="Does it bear on this?")
    for questions in ((rank, Predicate("order#1", "i")), (Predicate("order#1", "i"), rank)):
        gap = decisions.native_gap(Item("c", questions))
        assert "order#1" in gap and "order" in gap
        # Not a request `validate` refuses: a structured stage can answer it.
        decisions.validate([Item("c", questions)])
    assert "saw#0" in decisions.native_gap(Item("c", (Predicate("saw#0", "i"), _select())))



# --- 01e: MultiSelect ---------------------------------------------------------

SAW = (Option("characters:winifred", "Winifred", aliases=("Winnie",)),
       Option("characters:seraphine", "Seraphine"),
       Option("characters:mara", "Mara"))


def _select(**kw) -> MultiSelect:
    return MultiSelect("saw", "Who saw it?", SAW, **kw)


def _read_select(value, **kw) -> Answer:
    item = Item("ctx", (_select(**kw),))
    (result,) = decisions.parse(json.dumps({"0": {"answers": {"saw": value}}}), [item],
                                explain=False)
    return result.answers["saw"]


def test_validate_select_bounds():
    decisions.validate([Item("c", (_select(),))])
    decisions.validate([Item("c", (MultiSelect("s", "i", _options(1)),))])
    decisions.validate([Item("c", (MultiSelect("s", "i",
                                               _options(decisions.MAX_SELECT_OPTIONS)),))])
    for n in (0, decisions.MAX_SELECT_OPTIONS + 1):
        with pytest.raises(DecideRequestError, match="options"):
            decisions.validate([Item("c", (MultiSelect("s", "i", _options(n)),))])
    for kw in ({"min": 0, "max": 0}, {"min": 3}, {"min": 1, "max": 1}, {"max": 3}):
        decisions.validate([Item("c", (_select(**kw),))])
    for kw in ({"min": -1}, {"min": 4}, {"max": 4}, {"min": 2, "max": 1}, {"max": -1},
               {"min": True}, {"max": 1.0}):
        with pytest.raises(DecideRequestError, match="selects"):
            decisions.validate([Item("c", (_select(**kw),))])
    with pytest.raises(DecideRequestError, match="collides"):
        decisions.validate([Item("c", (MultiSelect("s", "i", (SAW[0], Option("winnie", ""))),))])


def test_select_max_zero_means_zero_not_all():
    assert _select(max=0).most == 0
    assert _select().most == 3
    assert _read_select([], max=0) == Answer(())
    assert _unreadable(_read_select(["characters:mara"], max=0))


def test_select_schema_and_counts():
    ids = [o.id for o in SAW]
    listed = {"type": "array", "items": {"type": "string", "enum": ids}}
    def answers(q):
        return decisions.schema([Item("c", (q,))], explain=False)["properties"]["0"][
            "properties"]["answers"]["properties"]
    assert answers(_select())["saw"] == listed
    assert answers(_select(allow_none=True))["saw"] == {"anyOf": [listed, {"type": "null"}]}
    assert "minItems" not in json.dumps(answers(_select(min=1, max=2)))
    assert decisions.enum_values(Item("c", (_select(), PRED))) == 3
    assert decisions.kinds([Item("c", (_select(),))]) == {"select"}


def test_parse_select_answers_in_option_order():
    assert _read_select(["characters:mara", "winnie"]) == Answer(
        ("characters:winifred", "characters:mara"))
    assert _read_select(["CHARACTERS:SERAPHINE"]) == Answer(("characters:seraphine",))
    answer = _read_select(["characters:mara"])
    assert answer.marginals is None and answer.distribution is None


def test_parse_select_empty_is_a_real_answer_not_abstention():
    answer = _read_select([])
    assert answer == Answer(())
    assert answer.answer == () and answer.answer is not None and not answer.reason
    assert _read_select(None, allow_none=True) == Answer(None, "abstained")
    assert _unreadable(_read_select(None))


def test_parse_select_refusals():
    unknown = _read_select(["characters:winifred", "characters:rowan"])
    assert _unreadable(unknown, decisions.NOT_AN_OPTION) and unknown.stated == ""
    duplicate = _read_select(["characters:winifred", "Winnie"])
    assert _unreadable(duplicate) and decisions.was_read(duplicate)
    assert _unreadable(_read_select([], min=1))
    assert _unreadable(_read_select(["characters:mara", "characters:winifred"], max=1))
    for value in ("characters:mara", {"a": 1}, 0, True):
        assert _unreadable(_read_select(value))


def test_outcome_and_render_spell_a_selection_as_a_list():
    item = Item("c", (_select(),))
    for value in ((), ("characters:winifred", "characters:mara")):
        results = (ItemResult({"saw": Answer(value)}),)
        assert decisions.outcome("structured", "", "", results)["items"][0]["answers"] == {
            "saw": {"answer": list(value)}}
        text = decisions.render(results, [item], explain=False)
        assert json.loads(text) == {"0": {"answers": {"saw": list(value)}}}
        (back,) = decisions.parse(text, [item], explain=False)
        assert back.answers["saw"] == Answer(value)


def test_native_gap_carries_a_selection():
    assert decisions.native_gap(Item("c", (PRED, _select()))) == ""



# --- 01e: Joint ---------------------------------------------------------------

STRIKE, HEAL, WAIT = Option("strike", "Strike"), Option("heal", "Heal"), Option("wait", "Wait")
SENTINEL = Option("creatures:sentinel", "the sentinel")
SMUGGLER = Option("creatures:smuggler", "the smuggler")
MARA_T = Option("characters:mara", "Mara")


def _joint(**kw) -> Joint:
    kw.setdefault("tails", (("strike", (SENTINEL, SMUGGLER)), ("heal", (MARA_T,))))
    return Joint("act", "What does Winifred do?", (STRIKE, HEAL, WAIT), **kw)


def _read_joint(value, **kw) -> Answer:
    item = Item("ctx", (_joint(**kw),))
    (result,) = decisions.parse(json.dumps({"0": {"answers": {"act": value}}}), [item],
                                explain=False)
    return result.answers["act"]


def test_joint_flattens_to_one_choice_of_legal_pairs():
    choice = _joint().choice
    assert choice == Choice("act", "What does Winifred do?", (
        Option("strike=>creatures:sentinel", "Strike -> the sentinel"),
        Option("strike=>creatures:smuggler", "Strike -> the smuggler"),
        Option("heal=>characters:mara", "Heal -> Mara"),
        Option("wait", "Wait")))
    # A head mapped to () takes no tail, as one missing from `tails` does.
    assert _joint(tails=(("heal", ()),)).choice.options == (
        Option("strike", "Strike"), Option("heal", "Heal"), Option("wait", "Wait"))
    assert _joint(allow_none=True).choice.allow_none
    assert _joint().KIND == "joint"


def test_joint_key_and_split_are_inverses():
    for pair in (Pair("strike", "creatures:sentinel"), Pair("wait", None)):
        assert decisions.split_joint(pair.key) == pair
    assert Pair("strike", "creatures:sentinel").key == "strike=>creatures:sentinel"
    assert decisions.joint_key("wait", None) == "wait"
    for bad in ("", "=>creatures:sentinel", "strike=>", "a=>b=>c"):
        with pytest.raises(ValueError):
            decisions.split_joint(bad)


def test_validate_joint_refusals():
    decisions.validate([Item("c", (_joint(),))])
    cases = [
        (_joint(tails=(("strike", (Option("a=>b", ""),)),)), "=>"),
        (Joint("j", "i", (Option("x=>y", ""), WAIT)), "=>"),
        (Joint("j", "i", (Option("strike", "", aliases=("hit",)), WAIT)), "aliases"),
        (_joint(tails=(("strike", (Option("creatures:sentinel", "", aliases=("s",)),)),)),
         "aliases"),
        (_joint(tails=(("dance", (SENTINEL,)),)), "not one of its heads"),
        (_joint(tails=((["strike"], (SENTINEL,)),)), "not one of its heads"),
        (_joint(tails=(("strike", (SENTINEL,)), ("strike", (SMUGGLER,)))), "twice"),
        (Joint("j", "i", (WAIT,)), "legal pairs"),
        (Joint("j", "i", (STRIKE, Option("Strike", ""))), "collides"),
        (_joint(tails=(("strike", (SENTINEL, SENTINEL)),)), "collides"),
        (Joint("j", "i", (STRIKE, Option(NONE_KEY, ""))), "reserved"),
    ]
    for q, match in cases:
        with pytest.raises(DecideRequestError, match=match):
            decisions.validate([Item("c", (q,))])
    # One legal pair is enough with allow_none, as for a choice.
    decisions.validate([Item("c", (Joint("j", "i", (WAIT,), allow_none=True),))])


def _wide_joint(qid: str = "act", heads: int = 15, tails: int = 17) -> Joint:
    return Joint(qid, "i", tuple(Option(f"h{h}", "") for h in range(heads)),
                 tuple((f"h{h}", tuple(Option(f"t{t}", "") for t in range(tails)))
                       for h in range(heads)))


def test_validate_joint_bounds_its_flattened_pairs():
    decisions.validate([Item("c", (_wide_joint(),))])          # 255 pairs
    with pytest.raises(DecideRequestError, match="legal pairs"):
        decisions.validate([Item("c", (_wide_joint(tails=18),))])  # 270


def test_joint_schema_is_a_flat_enum_and_counts_its_pairs():
    answers = decisions.schema([Item("c", (_joint(allow_none=True),))], explain=False)[
        "properties"]["0"]["properties"]["answers"]["properties"]
    assert answers["act"] == {"anyOf": [
        {"type": "string", "enum": ["strike=>creatures:sentinel", "strike=>creatures:smuggler",
                                    "heal=>characters:mara", "wait"]},
        {"type": "null"}]}
    assert decisions.enum_values(Item("c", (_joint(),))) == 4
    assert decisions.kinds([Item("c", (_joint(),))]) == {"joint"}


def test_chunks_hold_three_255_pair_joints():
    items = [Item("c", (_wide_joint(),)) for _ in range(5)]
    assert decisions.enum_values(items[0]) == 255
    assert [len(c) for _, c in decisions.chunks(items)] == [3, 2]


def test_parse_joint_splits_into_a_pair():
    answer = _read_joint("strike=>creatures:smuggler")
    assert answer == Answer(Pair("strike", "creatures:smuggler"))
    assert decisions.split_joint(answer.answer.key) == answer.answer
    assert _read_joint("WAIT") == Answer(Pair("wait", None))
    assert _read_joint(None, allow_none=True) == Answer(None, "abstained")
    assert _unreadable(_read_joint(None))
    # An illegal pair, and a bare head that takes a target, name no option.
    assert _unreadable(_read_joint("heal=>creatures:sentinel"), decisions.NOT_AN_OPTION)
    assert _unreadable(_read_joint("strike"), decisions.NOT_AN_OPTION)
    assert _unreadable(_read_joint(["strike", "creatures:sentinel"]), decisions.NOT_AN_OPTION)


def test_outcome_and_render_write_a_pair_as_its_key():
    item = Item("c", (_joint(),))
    dist = {"strike=>creatures:sentinel": 0.3, "strike=>creatures:smuggler": 0.3,
            "heal=>characters:mara": 0.4}
    results = (ItemResult({"act": Answer(Pair("heal", "characters:mara"),
                                         distribution=dist)}, backend="native"),)
    recorded = decisions.outcome("native", "p", "m", results)["items"][0]["answers"]["act"]
    assert recorded == {"answer": "heal=>characters:mara", "distribution": dist}
    text = decisions.render(results, [item], explain=False)
    assert json.loads(text) == {"0": {"answers": {"act": "heal=>characters:mara"}}}
    (back,) = decisions.parse(text, [item], explain=False)
    assert back.answers["act"].answer == Pair("heal", "characters:mara")


def test_head_marginal_and_head_first():
    q = _joint(allow_none=True)
    dist = {"strike=>creatures:sentinel": 0.3, "strike=>creatures:smuggler": 0.3,
            "heal=>characters:mara": 0.35, NONE_KEY: 0.05}
    answer = Answer(Pair("heal", "characters:mara"), distribution=dist)
    marginal = decisions.head_marginal(answer, q)
    assert list(marginal) == ["strike", "heal"]
    assert marginal["strike"] == pytest.approx(0.6) and marginal["heal"] == 0.35
    assert decisions.head_first(answer, q) == "strike"
    # The chosen pair's head leads: nothing to report.
    assert decisions.head_first(Answer(Pair("strike", "creatures:sentinel"),
                                       distribution=dist), q) is None
    # No distribution (a structured answer), or no pair: None.
    assert decisions.head_marginal(Answer(Pair("wait", None)), q) is None
    assert decisions.head_first(Answer(Pair("wait", None)), q) is None
    assert decisions.head_first(Answer(None, "abstained", distribution=dist), q) is None
    # The reserved none is never a head, however much mass it holds.
    mostly_none = {"heal=>characters:mara": 0.2, "wait": 0.1, NONE_KEY: 0.7}
    assert decisions.head_marginal(Answer(None, "abstained", distribution=mostly_none),
                                   q) == {"heal": 0.2, "wait": 0.1}
    assert decisions.head_first(Answer(Pair("wait", None), distribution=mostly_none),
                                q) == "heal"


def test_native_form_passes_plain_kinds_through_and_flattens_a_joint():
    plain = _item()
    lowered, lift = decisions.native_form(plain)
    assert lowered is plain
    assert lift.parts == (("over", ("over",)), ("who", ("who",)), ("tone", ("tone",)))
    item = Item("ctx", (PRED, _joint()))
    lowered, lift = decisions.native_form(item)
    assert lowered == Item("ctx", (PRED, _joint().choice))
    reply = ItemResult({"over": Answer(True), "act": Answer("strike=>creatures:sentinel")},
                       backend="native")
    assert decisions.native_lift(item, reply, lift) == ItemResult(
        {"over": Answer(True), "act": Answer(Pair("strike", "creatures:sentinel"))},
        backend="native")


def test_native_gap_names_a_joint_past_the_option_limit():
    assert decisions.native_gap(Item("c", (_wide_joint(),))) == ""
    wide = Joint("act", "i", _wide_joint().heads, _wide_joint().tails, allow_none=True)
    gap = decisions.native_gap(Item("c", (wide,)))
    assert "act" in gap and "256 legal pairs" in gap



# --- 01e: the pointwise lowering of a rank and a selection --------------------

POINTWISE = "Does this scene bear on Mara's question?"


def test_native_form_lowers_a_rank_to_one_predicate_per_candidate():
    rank = _rank(pointwise=POINTWISE)
    lowered, lift = decisions.native_form(Item("ctx", (PRED, rank)))
    assert lowered.questions == (
        PRED,
        Predicate("order#0", f"{POINTWISE}\n\nCandidate scenes:pier: The debt at the pier"),
        Predicate("order#1", f"{POINTWISE}\n\nCandidate scenes:ledger: "
                             f"The ledger changes hands"),
        Predicate("order#2", f"{POINTWISE}\n\nCandidate scenes:tide: The tide comes in"))
    assert lift.parts == (("over", ("over",)), ("order", ("order#0", "order#1", "order#2")))


def test_native_form_lowers_a_selection_with_the_fixed_wording():
    lowered, _ = decisions.native_form(Item("ctx", (_select(),)))
    assert [q.id for q in lowered.questions] == ["saw#0", "saw#1", "saw#2"]
    assert lowered.questions[1].instructions == (
        "Who saw it?\n\nOption characters:seraphine: Seraphine\n\n"
        "Is this option one of those selected?")
    assert lowered.questions[1].instructions == decisions.NATIVE_SELECT_TEXT.format(
        instructions="Who saw it?", id="characters:seraphine", description="Seraphine")


def _lift(q, answers) -> Answer:
    item = Item("ctx", (q,))
    lowered, lift = decisions.native_form(item)
    reply = ItemResult({low.id: a for low, a in zip(lowered.questions, answers, strict=True)},
                       backend="native")
    return decisions.native_lift(item, reply, lift).answers[q.id]


def _p(probability: float) -> Answer:
    return _native(PRED, probability=probability)


def test_native_rank_lifts_into_tiers_with_its_marginals():
    rank = _rank(pointwise=POINTWISE)
    answer = _lift(rank, [_p(0.4), _p(0.9), _p(0.1)])
    assert answer == Answer(Ranking(tiers=(("scenes:ledger",), ("scenes:pier",),
                                           ("scenes:tide",))),
                            marginals={"scenes:pier": 0.4, "scenes:ledger": 0.9,
                                       "scenes:tide": 0.1})
    assert answer.distribution is None and answer.answer.rest == ()


def test_native_rank_equal_probabilities_are_one_tier():
    answer = _lift(_rank(pointwise=POINTWISE), [_p(0.7), _p(0.2), _p(0.7)])
    assert answer.answer == Ranking(tiers=(("scenes:pier", "scenes:tide"), ("scenes:ledger",)))
    with pytest.raises(ValueError):
        answer.answer.flat()


def test_native_rank_places_a_half_and_never_abstains():
    answer = _lift(_rank(pointwise=POINTWISE, allow_none=True), [_p(0.5), _p(0.5), _p(0.5)])
    assert answer.answer == Ranking(tiers=(("scenes:pier", "scenes:ledger", "scenes:tide"),))
    # A top is satisfied by construction: every candidate is ranked.
    assert _lift(_rank(pointwise=POINTWISE, top=1), [_p(0.3), _p(0.6), _p(0.1)]).answer.rest == ()


def test_native_rank_with_a_candidate_unanswered_is_unreadable_with_marginals():
    for missing in (Answer(None, "unreadable"), Answer(None, "refused"),
                    Answer(True)):   # a bool with no probability cannot be placed
        answer = _lift(_rank(pointwise=POINTWISE), [_p(0.8), missing, _p(0.3)])
        assert answer == Answer(None, "unreadable",
                                marginals={"scenes:pier": 0.8, "scenes:tide": 0.3})
    assert _lift(_rank(pointwise=POINTWISE), [Answer(None, "unreadable")] * 3) == Answer(
        None, "unreadable")


def test_native_rank_all_refused_is_refused():
    assert _lift(_rank(pointwise=POINTWISE), [Answer(None, "refused")] * 3) == Answer(
        None, "refused")


def test_native_select_thresholds_each_option_in_option_order():
    answer = _lift(_select(), [_p(0.9), _p(0.2), _p(0.6)])
    assert answer == Answer(("characters:winifred", "characters:mara"),
                            marginals={"characters:winifred": 0.9,
                                       "characters:seraphine": 0.2, "characters:mara": 0.6})
    assert _lift(_select(), [_p(0.1), _p(0.2), _p(0.3)]).answer == ()


def test_native_select_at_a_half_abstains_only_with_allow_none():
    got = [_p(0.9), _p(0.5), _p(0.1)]
    marginals = {"characters:winifred": 0.9, "characters:seraphine": 0.5,
                 "characters:mara": 0.1}
    assert _lift(_select(allow_none=True), got) == Answer(None, "abstained",
                                                          marginals=marginals)
    assert _lift(_select(), got) == Answer(None, "unreadable", marginals=marginals)


def test_native_select_out_of_bounds_is_unreadable_and_never_repaired():
    got = [_p(0.9), _p(0.8), _p(0.7)]
    answer = _lift(_select(max=2), got)
    assert answer == Answer(None, "unreadable", marginals={
        "characters:winifred": 0.9, "characters:seraphine": 0.8, "characters:mara": 0.7})
    assert _lift(_select(min=1), [_p(0.1)] * 3).reason == "unreadable"


def test_native_select_refused_and_unanswered():
    assert _lift(_select(), [Answer(None, "refused")] * 3) == Answer(None, "refused")
    assert _lift(_select(), [_p(0.9), Answer(None, "refused"), _p(0.1)]) == Answer(
        None, "unreadable", marginals={"characters:winifred": 0.9, "characters:mara": 0.1})


def test_a_lowered_answer_never_carries_a_distribution():
    for q, got in ((_rank(pointwise=POINTWISE), [_p(0.4), _p(0.9), _p(0.1)]),
                   (_select(), [_p(0.9), _p(0.2), _p(0.6)])):
        answer = _lift(q, got)
        assert answer.distribution is None and answer.expected is None
        assert answer.marginals is not None
