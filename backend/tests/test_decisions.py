"""The decide() contract: its types, validation, batch schema, parser and chunks.

`grimoire.decisions` is a leaf (it imports nothing from the package), so these
tests need no store and no client: every case is a pure function of its input.
"""

from __future__ import annotations

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
#: An array (01e) carries `items` and nothing else: no `minItems`, `maxItems`
#: or `uniqueItems`, which one provider or the other refuses or never
#: documents -- the parser holds a list's count and uniqueness.
SHARED_SUBSET = {"type", "enum", "anyOf", "items", "required", "properties",
                 "additionalProperties"}


def _walk(node, path="$"):
    assert isinstance(node, dict), path
    assert set(node) <= SHARED_SUBSET, (path, set(node) - SHARED_SUBSET)
    assert not isinstance(node.get("type"), list), path
    assert node.get("type") in {"object", "array", "string", "boolean", "integer", "null",
                                None}, path
    assert ("items" in node) == (node.get("type") == "array"), path
    if "items" in node:
        _walk(node["items"], f"{path}[]")
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
        Item("ctx", (Rank("r", "i", (MARA, WINIFRED, GRIMOIRE)),
                     Rank("n", "i", (MARA, WINIFRED), top=1, allow_none=True))),
        Item("ctx", (MultiSelect("s", "i", (MARA, WINIFRED), min=1, max=1),
                     MultiSelect("n", "i", (MARA,), allow_none=True))),
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
    """Nothing from the package but `schemas` (01f-C3), which is a leaf of
    its own: the portable subset, its budgets and the reader are shared,
    never a reach into the store or the gateway."""
    assert _sibling_imports("decisions") == {"schemas"}
    assert _sibling_imports("schemas") == set()


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
    assert _native(TONE, distribution=dist) == Answer(2, distribution=dist)
    assert _native(TONE, distribution={"0": 0.1, "3": 0.9}) == Answer(None, "unreadable")


def test_native_answer_score_ignores_a_fractional_weighted_score():
    """I1: both providers return a probability-weighted, fractional `score`.
    It is never rounded into an answer: the answer is the argmax of the
    per-level probabilities, or nothing."""
    dist = {"0": 0.1, "1": 0.2, "2": 0.7}
    assert _native(TONE, chosen=1.4, distribution=dist) == Answer(2, distribution=dist)
    assert _native(TONE, chosen=1.4) == Answer(None, "unreadable")
    bimodal = {"0": 0.45, "1": 0.1, "2": 0.45}
    assert _native(TONE, chosen=1.0, distribution=bimodal) == Answer(
        None, "abstained", distribution=bimodal)


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


# --- 01e: the new reports, `expected` and `tiers` ------------------------------

def test_answer_checks_its_new_reports():
    Answer(None, "unreadable", marginals={"a": 0.0, "b": 1})
    Answer(2, expected=1.5)
    Answer(None, "abstained", expected=0)
    for bad in (
        lambda: Answer(None, "unreadable", marginals={"a": 1.2}),
        lambda: Answer(None, "unreadable", marginals={"a": True}),
        lambda: Answer(None, "unreadable", marginals={"a": float("nan")}),
        lambda: Answer(None, "unreadable", marginals={1: 0.5}),
        lambda: Answer(None, "unreadable", marginals=[("a", 0.5)]),
        lambda: Answer(2, expected=float("nan")),
        lambda: Answer(2, expected=float("inf")),
        lambda: Answer(2, expected=True),
        lambda: Answer(2, expected="1.5"),
    ):
        with pytest.raises(ValueError):
            bad()
    # `expected` is a reading of the compared distribution, so it is not
    # compared itself; marginals are a report of their own, and are.
    assert Answer(2, expected=1.5) == Answer(2)
    assert Answer(None, "unreadable", marginals={"a": 0.4}) != Answer(None, "unreadable")


def test_native_score_expected_is_the_mass_normalised_level():
    dist = {"0": 0.1, "1": 0.2, "2": 0.7}
    assert _native(TONE, distribution=dist).expected == pytest.approx(1.6)
    # An omitted level is unreported, not zero: it adds to neither sum.
    tied = _native(TONE, distribution={"1": 0.2, "2": 0.2})
    assert (tied.answer, tied.reason) == (None, "abstained")
    assert tied.expected == pytest.approx(1.5)
    # A tie between two levels still yields the level between them.
    bimodal = _native(TONE, distribution={"0": 0.45, "1": 0.1, "2": 0.45})
    assert bimodal.reason == "abstained" and bimodal.expected == pytest.approx(1.0)
    # Zero mass, no distribution, an invalid one: no expected level.
    assert _native(TONE, distribution={"0": 0.0, "1": 0.0}).expected is None
    assert _native(TONE, chosen=2).expected is None
    assert _native(TONE, distribution={"0": 0.1, "3": 0.9}).expected is None
    # A provider's weighted score is never the expected level.
    assert _native(TONE, chosen=1.4, distribution=dist).expected == pytest.approx(1.6)
    assert _native(TONE, chosen=1.4).expected is None
    # A chosen level keeps its distribution's expected level beside it.
    assert _native(TONE, chosen=0, distribution=dist).expected == pytest.approx(1.6)
    assert _native(TONE, refused=True, distribution=dist).expected is None
    # Only a score has one.
    assert _native(PRED, probability=0.8).expected is None
    assert _native(WHO, distribution={"characters:mara": 0.2, "grimoire": 0.8}).expected is None


def test_a_structured_answer_carries_no_marginals_or_expected():
    text = json.dumps({"0": {"answers": {"over": True, "who": None, "tone": 1},
                             "rationale": "r"}})
    for answers in _answers(text, [_item()]):
        for answer in answers.values():
            assert answer.marginals is None and answer.expected is None


def test_tiers_groups_exact_ties_in_mapping_order():
    assert decisions.tiers({"b": 0.2, "a": 0.9, "c": 0.2, "d": 0.5}) == (
        ("a",), ("d",), ("b", "c"))
    assert decisions.tiers({}) == ()


def test_tiers_ties_within_mass_tie():
    # Float error in a summed probability never decides an order.
    summed = 0.1 + 0.2
    assert summed != 0.3
    assert decisions.tiers({"x": 0.3, "y": summed}) == (("x", "y"),)
    assert decisions.tiers({"x": 0.3, "y": 0.3 + 2 * decisions.MASS_TIE}) == (("y",), ("x",))


def test_tiers_anchor_on_the_tiers_highest_value():
    """Greedy from the top: three values each just inside tolerance of the
    next do not chain into one tier."""
    assert decisions.tiers({"a": 3.0, "b": 2.1, "c": 1.2}, tolerance=1.0) == (
        ("a", "b"), ("c",))
    assert decisions.tiers({"c": 2.0, "a": 3.0, "b": 2.0}, tolerance=1.0) == (("c", "a", "b"),)


def test_tiers_takes_integer_levels():
    assert decisions.tiers({"mara": 7, "winifred": 7, "seraphine": 9, "rowan": 2}) == (
        ("seraphine",), ("mara", "winifred"), ("rowan",))


def test_tiers_refuses_a_value_it_cannot_place():
    for bad in (float("nan"), True, "0.5", None, float("inf")):
        with pytest.raises(ValueError):
            decisions.tiers({"a": 0.5, "b": bad})


def test_outcome_writes_marginals_and_expected():
    dist = {"0": 0.1, "1": 0.2, "2": 0.7}
    score = _native(TONE, distribution=dist)
    results = (ItemResult({"tone": score,
                           "rank": Answer(None, "unreadable", marginals={"a": 0.25})},
                          backend="native"),)
    out = decisions.outcome("native", "openai", "m", results)
    answers = out["items"][0]["answers"]
    assert answers["tone"] == {"answer": 2, "distribution": dist,
                               "expected": pytest.approx(1.6)}
    assert answers["rank"] == {"answer": None, "reason": "unreadable",
                               "marginals": {"a": 0.25}}
    json.dumps(out)


# --- 01e: Rank, on the structured path ----------------------------------------

SCENES = tuple(Option(f"scene:{name}", f"The {name} scene") for name in
               ("pier", "market", "storm", "ledger", "tide"))


def _rank(n: int = 3, **kw) -> Rank:
    return Rank("relevant", "Order them", SCENES[:n], **kw)


def _rank_answer(value, rank: Rank | None = None) -> Answer:
    rank = rank or _rank()
    (result,) = decisions.parse(json.dumps({"0": {"answers": {rank.id: value}}}),
                                [Item("ctx", (rank,))], explain=False)
    return result.answers[rank.id]


def test_question_kinds_are_class_constants():
    import dataclasses
    assert [cls.KIND for cls in (Predicate, Choice, Score, Rank, MultiSelect)] == [
        "predicate", "choice", "score", "rank", "select"]
    for cls in (Predicate, Choice, Score, Rank, MultiSelect):
        assert "KIND" not in {f.name for f in dataclasses.fields(cls)}
    assert _rank().KIND == "rank"
    assert decisions.kinds([_item(), Item("c", (_rank(), Predicate("p", "i")))]) == (
        "predicate", "choice", "score", "rank")


def test_validate_rank_bounds():
    candidates = tuple(Option(f"c{i}", "") for i in range(decisions.MAX_RANK_CANDIDATES + 1))
    assert decisions.MAX_RANK_CANDIDATES == 32
    for n in (2, decisions.MAX_RANK_CANDIDATES):
        decisions.validate([Item("c", (Rank("r", "i", candidates[:n]),))])
    for n in (1, decisions.MAX_RANK_CANDIDATES + 1):
        with pytest.raises(DecideRequestError, match="candidates"):
            decisions.validate([Item("c", (Rank("r", "i", candidates[:n]),))])
    for top in (1, 3):
        decisions.validate([Item("c", (_rank(top=top),))])
    for top in (0, 4, True, -1, 1.0):
        with pytest.raises(DecideRequestError, match="top"):
            decisions.validate([Item("c", (_rank(top=top),))])
    collide = (Option("scene:pier", "", aliases=("the pier",)),
               Option("the_pier", ""))
    with pytest.raises(DecideRequestError, match="collides"):
        decisions.validate([Item("c", (Rank("r", "i", collide),))])
    with pytest.raises(DecideRequestError, match="reserved"):
        decisions.validate([Item("c", (Rank("r", "i", (MARA, Option(NONE_KEY, ""))),))])
    with pytest.raises(DecideRequestError, match="not an Option"):
        decisions.validate([Item("c", (Rank("r", "i", (MARA, "characters:winifred")),))])


def test_rank_schema_is_an_array_of_its_ids():
    answers = decisions.schema([Item("c", (
        _rank(), Rank("maybe", "i", SCENES[:2], allow_none=True)))], explain=False)
    props = answers["properties"]["0"]["properties"]["answers"]["properties"]
    assert props["relevant"] == {"type": "array", "items": {
        "type": "string", "enum": ["scene:pier", "scene:market", "scene:storm"]}}
    assert props["maybe"] == {"anyOf": [
        {"type": "array", "items": {"type": "string",
                                    "enum": ["scene:pier", "scene:market"]}},
        {"type": "null"}]}


def test_enum_values_and_schema_chars_count_rank_candidates():
    item = Item("c", (_rank(5), Predicate("p", "i")))
    assert decisions.enum_values(item) == 5
    plain = Item("c", (Predicate("p", "i"),))
    assert decisions.schema_chars([item]) - decisions.schema_chars([plain]) == (
        len("relevant") + sum(len(opt.id) for opt in SCENES))


def test_parse_rank_reads_exact_aliased_and_normalised_entries():
    aliased = Rank("relevant", "i", (Option("scene:pier", "", aliases=("The Pier",)),
                                     Option("scene:market", ""), Option("scene:storm", "")))
    answer = _rank_answer(["the-pier", "Scene:Storm", "scene:market"], aliased)
    assert answer == Answer(Ranking((("scene:pier",), ("scene:storm",), ("scene:market",))))
    assert answer.marginals is None and answer.expected is None and answer.stated == ""


def test_parse_rank_unknown_entry_is_not_an_option_and_is_never_dropped():
    answer = _rank_answer(["scene:pier", "scene:moon", "scene:storm", "scene:market"])
    assert _unreadable(answer, decisions.NOT_AN_OPTION)
    assert answer.stated == "" and decisions.was_read(answer)
    assert _unreadable(_rank_answer(["scene:pier", 3, "scene:storm"]), decisions.NOT_AN_OPTION)
    # An unknown entry beside a duplicate: a value naming nothing was given.
    assert _unreadable(_rank_answer(["scene:pier", "scene:pier", "scene:moon"]),
                       decisions.NOT_AN_OPTION)


def test_parse_rank_duplicate_is_unreadable_and_was_read():
    answer = _rank_answer(["scene:pier", "scene:storm", "Scene:Pier"])
    assert _unreadable(answer) and decisions.was_read(answer)


def test_parse_rank_too_short_is_unreadable():
    assert _unreadable(_rank_answer(["scene:pier", "scene:storm"]))
    assert _unreadable(_rank_answer([]))
    assert _unreadable(_rank_answer(["scene:pier"], _rank(top=2)))
    top2 = _rank_answer(["scene:storm", "scene:pier"], _rank(top=2))
    assert top2 == Answer(Ranking((("scene:storm",), ("scene:pier",)), rest=("scene:market",)))
    # Ranking past `top` is an answer, not an error: rest is what was left out.
    assert _rank_answer(["scene:storm", "scene:pier", "scene:market"], _rank(top=1)) == Answer(
        Ranking((("scene:storm",), ("scene:pier",), ("scene:market",))))
    rest_order = _rank_answer(["scene:ledger"], _rank(5, top=1))
    assert rest_order.answer.rest == ("scene:pier", "scene:market", "scene:storm", "scene:tide")


def test_parse_rank_null_and_wrong_types():
    assert _rank_answer(None, _rank(allow_none=True)) == Answer(None, "abstained")
    assert _unreadable(_rank_answer(None))
    for value in ("scene:pier, scene:storm", {"0": "scene:pier"}, 1, True):
        assert _unreadable(_rank_answer(value))


def test_ranking_flat_and_position():
    strict = Ranking((("a",), ("b",)), rest=("c", "d"))
    assert strict.flat() == ("a", "b")         # rest is not ranked, so not flattened
    assert strict.position("b") == 1
    assert strict.position("c") is None and strict.position("zz") is None
    tied = Ranking((("a",), ("c", "b"), ("d",)))
    with pytest.raises(ValueError, match="tie-break"):
        tied.flat()
    assert tied.flat(tiebreak=lambda c: c) == ("a", "b", "c", "d")
    assert tied.flat(tiebreak=["c", "b"].index) == ("a", "c", "b", "d")
    assert tied.position("b") == tied.position("c") == 1


def test_outcome_and_render_spell_a_ranking():
    rank = _rank()
    items = [Item("ctx", (rank,))]
    strict = Ranking((("scene:storm",), ("scene:pier",), ("scene:market",)))
    tied = Ranking((("scene:storm", "scene:pier"), ("scene:market",)))
    results = (ItemResult({"relevant": Answer(strict)}, backend="native"),)
    assert decisions.outcome("native", "p", "m", results)["items"][0]["answers"] == {
        "relevant": {"answer": {"tiers": [["scene:storm"], ["scene:pier"], ["scene:market"]],
                                "rest": []}}}
    text = decisions.render(results, items, explain=False)
    assert json.loads(text) == {"0": {"answers": {
        "relevant": ["scene:storm", "scene:pier", "scene:market"]}}}
    (back,) = decisions.parse(text, items, explain=False)
    assert back.answers["relevant"] == Answer(strict)
    tied_text = decisions.render((ItemResult({"relevant": Answer(tied)}),), items,
                                 explain=False)
    assert json.loads(tied_text) == {"0": {"answers": {"relevant": None}}}
    out = decisions.outcome("native", "p", "m", (ItemResult({"relevant": Answer(tied)}),))
    assert out["items"][0]["answers"]["relevant"]["answer"]["tiers"] == [
        ["scene:storm", "scene:pier"], ["scene:market"]]
    json.dumps(out)


def test_native_gap_names_a_rank_without_pointwise():
    gap = decisions.native_gap(Item("c", (Predicate("p", "i"), _rank())))
    assert gap == ("Question relevant ranks its candidates and names no pointwise question; "
                   "a decisions endpoint cannot order them.")
    assert decisions.native_gap(Item("c", (Predicate("p", "i"),
                                           _rank(pointwise="Relevant?")))) == ""


# --- 01e: MultiSelect, on the structured path ---------------------------------

CAST = (MARA, WINIFRED, Option("characters:seraphine", "Seraphine", aliases=("Sera",)))


def _select(**kw) -> MultiSelect:
    return MultiSelect("saw", "Who saw it?", kw.pop("options", CAST), **kw)


def _select_answer(value, select: MultiSelect | None = None) -> Answer:
    select = select or _select()
    (result,) = decisions.parse(json.dumps({"0": {"answers": {select.id: value}}}),
                                [Item("ctx", (select,))], explain=False)
    return result.answers[select.id]


def test_validate_select_bounds():
    options = tuple(Option(f"o{i}", "") for i in range(decisions.MAX_SELECT_OPTIONS + 1))
    assert decisions.MAX_SELECT_OPTIONS == 32
    for n in (1, decisions.MAX_SELECT_OPTIONS):
        decisions.validate([Item("c", (MultiSelect("s", "i", options[:n]),))])
    for n in (0, decisions.MAX_SELECT_OPTIONS + 1):
        with pytest.raises(DecideRequestError, match="options"):
            decisions.validate([Item("c", (MultiSelect("s", "i", options[:n]),))])
    for kw in ({}, {"min": 3}, {"max": 0}, {"min": 1, "max": 1}, {"min": 0, "max": 3}):
        decisions.validate([Item("c", (_select(**kw),))])
    for kw in ({"min": 4}, {"max": 4}, {"min": 2, "max": 1}, {"min": -1}, {"max": -1},
               {"min": True}, {"max": False}, {"min": 1.0}):
        with pytest.raises(DecideRequestError, match="min"):
            decisions.validate([Item("c", (_select(**kw),))])
    with pytest.raises(DecideRequestError, match="collides"):
        clash = Option("x", "", aliases=("Characters:Mara",))
        decisions.validate([Item("c", (_select(options=(MARA, clash)),))])


def test_select_schema_is_an_array_of_its_ids():
    out = decisions.schema([Item("c", (_select(), MultiSelect(
        "maybe", "i", CAST[:2], allow_none=True)))], explain=False)
    props = out["properties"]["0"]["properties"]["answers"]["properties"]
    ids = ["characters:mara", "characters:winifred", "characters:seraphine"]
    assert props["saw"] == {"type": "array", "items": {"type": "string", "enum": ids}}
    assert props["maybe"] == {"anyOf": [
        {"type": "array", "items": {"type": "string", "enum": ids[:2]}}, {"type": "null"}]}
    assert decisions.enum_values(Item("c", (_select(), Predicate("p", "i")))) == 3


def test_parse_select_answers_in_option_order():
    assert _select_answer(["Sera", "characters:mara"]) == Answer(
        ("characters:mara", "characters:seraphine"))
    assert _select_answer([" Characters:Winifred "]) == Answer(("characters:winifred",))
    answer = _select_answer(["characters:mara"])
    assert answer.marginals is None and answer.expected is None


def test_the_empty_selection_is_an_answer_not_an_abstention():
    empty = _select_answer([])
    assert empty == Answer(()) and empty.answer is not None and empty.reason == ""
    assert _select_answer([], _select(max=0)) == Answer(())
    assert _unreadable(_select_answer(["characters:mara"], _select(max=0)))
    assert _select_answer(None, _select(allow_none=True)) == Answer(None, "abstained")
    assert _unreadable(_select_answer(None))


def test_parse_select_out_of_bounds_is_unreadable_never_repaired():
    assert _unreadable(_select_answer([], _select(min=1)))
    two = ["characters:mara", "characters:winifred"]
    assert _unreadable(_select_answer(two, _select(max=1)))
    assert _select_answer(two, _select(min=2, max=2)) == Answer(tuple(two))


def test_parse_select_duplicate_unknown_and_non_list():
    dup = _select_answer(["characters:mara", "Characters:Mara"])
    assert _unreadable(dup) and decisions.was_read(dup)
    unknown = _select_answer(["characters:rowan"])
    assert _unreadable(unknown, decisions.NOT_AN_OPTION) and unknown.stated == ""
    for value in ("characters:mara", {"characters:mara": True}, True, 0):
        assert _unreadable(_select_answer(value))


def test_outcome_and_render_spell_a_selection():
    items = [Item("ctx", (_select(),))]
    for chosen in (("characters:mara", "characters:seraphine"), ()):
        results = (ItemResult({"saw": Answer(chosen)}, backend="native"),)
        out = decisions.outcome("native", "p", "m", results)
        assert out["items"][0]["answers"]["saw"] == {"answer": list(chosen)}
        text = decisions.render(results, items, explain=False)
        assert json.loads(text) == {"0": {"answers": {"saw": list(chosen)}}}
        (back,) = decisions.parse(text, items, explain=False)
        assert back.answers["saw"] == Answer(chosen)


def test_native_gap_takes_a_select():
    assert decisions.native_gap(Item("c", (Predicate("p", "i"), _select()))) == ""


# --- 01e: Joint, and the native lowering ----------------------------------------

STRIKE = Option("strike", "Strike")
HEAL = Option("heal", "Tend a wound")
WITHDRAW = Option("withdraw", "Withdraw along the pier")
SENTINEL = Option("creatures:sentinel", "The sentinel")
GULLS = Option("creatures:gulls", "The gull swarm")
MARA_HURT = Option("characters:mara", "Mara, bleeding")


def _joint(**kw) -> Joint:
    return Joint("act", "What does Seraphine do?", kw.pop("heads", (STRIKE, HEAL, WITHDRAW)),
                 kw.pop("tails", (("strike", (SENTINEL, GULLS)), ("heal", (MARA_HURT,)))),
                 **kw)


def _joint_answer(value, joint: Joint | None = None) -> Answer:
    joint = joint or _joint()
    (result,) = decisions.parse(json.dumps({"0": {"answers": {joint.id: value}}}),
                                [Item("ctx", (joint,))], explain=False)
    return result.answers[joint.id]


def _wide_joint(pairs: int, *, allow_none: bool = False, qid: str = "act") -> Joint:
    """One head with `pairs` targets."""
    return Joint(qid, "i", (STRIKE,),
                 (("strike", tuple(Option(f"t{i:03d}", "") for i in range(pairs))),),
                 allow_none=allow_none)


def test_joint_key_and_split_joint_are_inverse():
    assert decisions.JOINT_SEP == "=>"
    for pair in (Pair("strike", "creatures:sentinel"), Pair("withdraw", None)):
        assert decisions.split_joint(pair.key) == pair
    assert Pair("strike", "creatures:sentinel").key == "strike=>creatures:sentinel"
    assert Pair("withdraw", None).key == decisions.joint_key("withdraw", None) == "withdraw"
    for bad in ("", "=>creatures:sentinel", "strike=>"):
        with pytest.raises(ValueError):
            decisions.split_joint(bad)


def test_a_joint_flattens_to_one_choice_over_its_legal_pairs():
    choice = _joint().choice
    assert choice == decisions.joint_choice(_joint())
    assert (choice.id, choice.instructions, choice.allow_none) == (
        "act", "What does Seraphine do?", False)
    assert [(o.id, o.description) for o in choice.options] == [
        ("strike=>creatures:sentinel", "Strike -> The sentinel"),
        ("strike=>creatures:gulls", "Strike -> The gull swarm"),
        ("heal=>characters:mara", "Tend a wound -> Mara, bleeding"),
        ("withdraw", "Withdraw along the pier")]
    # A head mapped to no targets takes no tail, as one missing from tails does.
    empty = _joint(tails=(("strike", (SENTINEL,)), ("heal", ())))
    assert [o.id for o in empty.choice.options] == [
        "strike=>creatures:sentinel", "heal", "withdraw"]
    assert _joint().KIND == "joint"


def test_validate_joint_refuses_its_own_cases():
    decisions.validate([Item("c", (_joint(),))])
    cases = {
        "holds": [_joint(heads=(Option("strike=>x", ""), HEAL)),
                  _joint(tails=(("strike", (Option("a=>b", ""),)),))],
        "alias": [_joint(heads=(Option("strike", "", ("hit",)), HEAL)),
                  _joint(tails=(("strike", (Option("creatures:sentinel", "", ("it",)),)),))],
        "not one of its heads": [_joint(tails=(("flee", (SENTINEL,)),))],
        "twice": [_joint(tails=(("strike", (SENTINEL,)), ("strike", (GULLS,))))],
        "pairs": [_joint(tails=("strike",)), _joint(tails=(("strike", [SENTINEL]),))],
        "not an Option": [_joint(heads=(STRIKE, "heal"))],
    }
    for match, joints in cases.items():
        for joint in joints:
            with pytest.raises(DecideRequestError, match=match):
                decisions.validate([Item("c", (joint,))])


def test_validate_joint_holds_the_flattened_choice_bounds():
    decisions.validate([Item("c", (_wide_joint(decisions.MAX_OPTIONS),))])
    decisions.validate([Item("c", (_wide_joint(1, allow_none=True),))])
    for joint in (_wide_joint(decisions.MAX_OPTIONS + 1), _wide_joint(1),
                  Joint("act", "i", (), ())):
        with pytest.raises(DecideRequestError, match="joint 'act' offers"):
            decisions.validate([Item("c", (joint,))])
    # Two pairs that collide once normalised are one pair to the parser.
    clash = Joint("act", "i", (STRIKE,), (("strike", (Option("a b", ""), Option("a_b", ""))),))
    with pytest.raises(DecideRequestError, match="collides"):
        decisions.validate([Item("c", (clash,))])


def test_joint_schema_is_a_flat_enum_of_pair_keys():
    maybe = Joint("maybe", "i", (STRIKE, WITHDRAW), (("strike", (SENTINEL,)),),
                  allow_none=True)
    out = decisions.schema([Item("c", (_joint(), maybe))], explain=False)
    props = out["properties"]["0"]["properties"]["answers"]["properties"]
    assert props["act"] == {"type": "string", "enum": [
        "strike=>creatures:sentinel", "strike=>creatures:gulls", "heal=>characters:mara",
        "withdraw"]}
    assert props["maybe"] == {"anyOf": [
        {"type": "string", "enum": ["strike=>creatures:sentinel", "withdraw"]},
        {"type": "null"}]}
    assert decisions.enum_values(Item("c", (_joint(),))) == 4


def test_parse_joint_splits_into_a_pair():
    assert _joint_answer("heal=>characters:mara") == Answer(Pair("heal", "characters:mara"))
    assert _joint_answer(" Strike=>Creatures:Gulls ") == Answer(Pair("strike", "creatures:gulls"))
    assert _joint_answer("withdraw") == Answer(Pair("withdraw", None))
    pair = _joint_answer("strike=>creatures:sentinel").answer
    assert decisions.split_joint(pair.key) == pair
    # A head alone, where the head takes a target, names no legal pair.
    for value in ("heal", "heal=>creatures:sentinel", ["strike", "creatures:sentinel"]):
        unread = _joint_answer(value)
        assert _unreadable(unread, decisions.NOT_AN_OPTION) and unread.stated == ""
    assert _joint_answer(None, _joint(allow_none=True)) == Answer(None, "abstained")
    assert _unreadable(_joint_answer(None))


def test_chunks_hold_three_full_joints():
    """At 255 pairs each, three joints are 765 enum values; a fourth would
    pass 1000, so `chunks` opens the next chunk there."""
    items = [Item("c", (_wide_joint(decisions.MAX_OPTIONS, qid=f"act{i}"),))
             for i in range(4)]
    for item in items:
        assert decisions.enum_values(item) == 255
    decisions.validate(items)
    assert [(start, len(chunk)) for start, chunk in decisions.chunks(items)] == [(0, 3), (3, 1)]


def test_outcome_and_render_spell_a_pair_as_its_key():
    items = [Item("ctx", (_joint(),))]
    for pair in (Pair("strike", "creatures:gulls"), Pair("withdraw", None)):
        results = (ItemResult({"act": Answer(pair)}, backend="native"),)
        out = decisions.outcome("native", "p", "m", results)
        assert out["items"][0]["answers"]["act"] == {"answer": pair.key}
        text = decisions.render(results, items, explain=False)
        assert json.loads(text) == {"0": {"answers": {"act": pair.key}}}
        (back,) = decisions.parse(text, items, explain=False)
        assert back.answers["act"] == Answer(pair)


def _native_joint(distribution, chosen=decisions.UNSTATED, joint: Joint | None = None):
    """A native joint answer, as an adapter's lift produces it."""
    joint = joint or _joint(allow_none=True)
    lowered, lift = decisions.native_form(Item("ctx", (joint,)))
    (choice,) = lowered.questions
    answered = ItemResult({joint.id: decisions.native_answer(
        choice, chosen=chosen, distribution=distribution)}, backend="native")
    return decisions.native_lift(Item("ctx", (joint,)), answered, lift).answers[joint.id]


def test_head_marginal_and_head_first():
    """0.3 on each of two strikes and 0.4 on one heal: the argmax pair is a
    heal, and the action most mass favours is a strike."""
    dist = {"strike=>creatures:sentinel": 0.3, "strike=>creatures:gulls": 0.3,
            "heal=>characters:mara": 0.4}
    answer = _native_joint(dist)
    assert answer.answer == Pair("heal", "characters:mara")
    assert answer.distribution == dist          # kept by the pair's key
    joint = _joint(allow_none=True)
    assert decisions.head_marginal(answer, joint) == pytest.approx({"strike": 0.6, "heal": 0.4})
    assert decisions.head_first(answer, joint) == "strike"
    # The chosen pair's head is the head most mass favours: nothing to say.
    agreed = _native_joint({"strike=>creatures:sentinel": 0.5, "heal=>characters:mara": 0.2})
    assert decisions.head_first(agreed, joint) is None


def test_head_first_excludes_the_reserved_none():
    dist = {"strike=>creatures:sentinel": 0.15, "strike=>creatures:gulls": 0.15,
            "heal=>characters:mara": 0.25, NONE_KEY: 0.45}
    joint = _joint(allow_none=True)
    chosen = _native_joint(dist, chosen="heal=>characters:mara")
    assert chosen.answer == Pair("heal", "characters:mara")
    assert decisions.head_marginal(chosen, joint) == pytest.approx({"strike": 0.3, "heal": 0.25})
    assert decisions.head_first(chosen, joint) == "strike"
    # The none's mass wins the argmax: abstained, no pair, nothing to regroup.
    abstained = _native_joint(dist)
    assert abstained.reason == "abstained" and decisions.head_first(abstained, joint) is None
    assert decisions.head_marginal(abstained, joint) == pytest.approx(
        {"strike": 0.3, "heal": 0.25})
    # A structured answer reports nothing.
    assert decisions.head_marginal(Answer(Pair("strike", "creatures:gulls")), joint) is None
    assert decisions.head_first(Answer(Pair("strike", "creatures:gulls")), joint) is None


def test_native_form_passes_old_items_through():
    item = _item()
    lowered, lift = decisions.native_form(item)
    assert lowered == item
    assert lift == decisions.Lift((("over", ("over",)), ("who", ("who",)), ("tone", ("tone",))))
    mixed = Item("ctx", (Predicate("over", "i"), _joint()))
    lowered, lift = decisions.native_form(mixed)
    assert lowered.questions == (Predicate("over", "i"), _joint().choice)
    assert lift.lowered[1] == ("act", ("act",))


def test_native_gap_counts_a_joints_pairs_with_none():
    assert decisions.native_gap(Item("c", (_wide_joint(255),))) == ""
    gap = decisions.native_gap(Item("c", (_wide_joint(255, allow_none=True),)))
    assert "act" in gap and "256" in gap


def test_native_form_lowers_a_rank_and_a_select_to_predicates():
    rank = _rank(pointwise="Does this scene bear on the turn?")
    select = _select(options=CAST[:2])
    item = Item("ctx", (Predicate("over", "i"), rank, select))
    lowered, lift = decisions.native_form(item)
    assert lowered.context == "ctx"
    assert lowered.questions == (
        Predicate("over", "i"),
        Predicate("relevant#0", "Does this scene bear on the turn?\n\n"
                                "Candidate scene:pier: The pier scene"),
        Predicate("relevant#1", "Does this scene bear on the turn?\n\n"
                                "Candidate scene:market: The market scene"),
        Predicate("relevant#2", "Does this scene bear on the turn?\n\n"
                                "Candidate scene:storm: The storm scene"),
        Predicate("saw#0", "Who saw it?\n\nOption characters:mara: Mara\n\n"
                           "Is this option one of those selected?"),
        Predicate("saw#1", "Who saw it?\n\nOption characters:winifred: Winifred\n\n"
                           "Is this option one of those selected?"))
    assert lift == decisions.Lift((("over", ("over",)),
                                   ("relevant", ("relevant#0", "relevant#1", "relevant#2")),
                                   ("saw", ("saw#0", "saw#1"))))
    # The caller's text is a value, never a template.
    braces = MultiSelect("s", "Pick {any}", (Option("o", "{id}"),))
    (low,) = decisions.native_form(Item("c", (braces,)))[0].questions
    assert low.instructions.startswith("Pick {any}\n\nOption o: {id}")
    with pytest.raises(ValueError, match="pointwise"):
        decisions.native_form(Item("c", (_rank(),)))


def test_native_gap_names_a_lowered_id_that_collides():
    rank = _rank(pointwise="Relevant?")
    for questions in ((rank, Predicate("relevant#1", "i")),
                      (Predicate("relevant#1", "i"), rank),
                      (_select(), Predicate("saw#0", "i"))):
        gap = decisions.native_gap(Item("c", questions))
        lowered = "saw" if questions[0].id == "saw" else "relevant"
        assert gap.startswith(f"Question {lowered} is asked natively as "), gap
        assert "already uses" in gap
    # An id holding `#` is legal; only a collision is refused.
    assert decisions.native_gap(Item("c", (rank, Predicate("relevant#9", "i")))) == ""


def _lifted(q, answers: list[Answer]) -> Answer:
    item = Item("ctx", (q,))
    lowered, lift = decisions.native_form(item)
    result = ItemResult({low.id: a for low, a in zip(lowered.questions, answers, strict=True)},
                        backend="native")
    return decisions.native_lift(item, result, lift).answers[q.id]


def _p(*ps) -> list[Answer]:
    return [_native(PRED, probability=p) for p in ps]


def test_a_native_rank_is_its_candidates_tiers_by_p_true():
    rank = _rank(pointwise="Relevant?", top=1, allow_none=True)
    answer = _lifted(rank, _p(0.2, 0.9, 0.5))
    assert answer.answer == Ranking((("scene:market",), ("scene:storm",), ("scene:pier",)))
    assert answer.marginals == {"scene:pier": 0.2, "scene:market": 0.9, "scene:storm": 0.5}
    assert answer.distribution is None and answer.probability is None
    # Equal P(true) is one tier, never broken; 0.5 places a candidate.
    tied = _lifted(rank, _p(0.7, 0.7, 0.5))
    assert tied.answer == Ranking((("scene:pier", "scene:market"), ("scene:storm",)))
    with pytest.raises(ValueError):
        tied.answer.flat()


def test_a_native_rank_with_a_candidate_unanswered_is_unreadable():
    rank = _rank(pointwise="Relevant?")
    missing = _lifted(rank, [*_p(0.8, 0.3), Answer(None, "unreadable")])
    assert (missing.answer, missing.reason, missing.detail) == (None, "unreadable", "")
    assert missing.marginals == {"scene:pier": 0.8, "scene:market": 0.3}
    part = _lifted(rank, [*_p(0.8, 0.3), Answer(None, "refused")])
    assert part.reason == "unreadable" and part.marginals == {"scene:pier": 0.8,
                                                              "scene:market": 0.3}
    refused = _lifted(rank, [Answer(None, "refused")] * 3)
    assert refused == Answer(None, "refused") and refused.marginals is None
    # A bool chosen with no probability cannot be placed.
    blind = _lifted(rank, [Answer(True), *_p(0.3, 0.2)])
    assert blind.reason == "unreadable"


def test_a_native_select_is_its_options_answered_true():
    select = _select(max=2)
    answer = _lifted(select, _p(0.8, 0.1, 0.6))
    assert answer.answer == ("characters:mara", "characters:seraphine")
    assert answer.marginals == {"characters:mara": 0.8, "characters:winifred": 0.1,
                                "characters:seraphine": 0.6}
    assert answer.distribution is None
    assert _lifted(select, _p(0.1, 0.2, 0.3)) == Answer((), marginals={
        "characters:mara": 0.1, "characters:winifred": 0.2, "characters:seraphine": 0.3})


def test_a_native_select_at_one_half_abstains_only_with_allow_none():
    at_half = _p(0.8, 0.5, 0.1)
    assert _lifted(_select(allow_none=True), at_half).reason == "abstained"
    strict = _lifted(_select(), at_half)
    assert (strict.answer, strict.reason, strict.detail) == (None, "unreadable", "")
    assert strict.marginals == {"characters:mara": 0.8, "characters:winifred": 0.5,
                                "characters:seraphine": 0.1}


def test_a_native_select_out_of_bounds_is_unreadable_never_repaired():
    three = _p(0.9, 0.8, 0.7)
    over = _lifted(_select(max=2), three)
    assert over.reason == "unreadable" and over.marginals["characters:seraphine"] == 0.7
    under = _lifted(_select(min=1), _p(0.1, 0.2, 0.3))
    assert under.reason == "unreadable" and under.marginals is not None
    assert _lifted(_select(), [Answer(None, "refused")] * 3) == Answer(None, "refused")
    assert _lifted(_select(), [*_p(0.9, 0.1), Answer(None, "refused")]).reason == "unreadable"


def test_native_questions_are_the_three_an_endpoint_has():
    assert decisions.native_questions(_item()) == _item().questions
    with pytest.raises(ValueError, match="relevant"):
        decisions.native_questions(Item("c", (Predicate("p", "i"), _rank())))
