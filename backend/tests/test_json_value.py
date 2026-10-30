"""Immutable JSON-like values that keep unknown authored fields verbatim."""

import pytest

from grimoire.core.json_value import JsonObject, JsonValueError, freeze, thaw

NESTED = {"b": [1, {"x": None, "y": [True, 2.5]}], "a": 1.5, "s": "text"}


def test_freeze_is_order_insensitive_hashable_and_round_trips():
    frozen = freeze(NESTED)
    reordered = freeze({"s": "text", "a": 1.5, "b": [1, {"y": [True, 2.5], "x": None}]})
    assert isinstance(frozen, JsonObject)
    assert frozen == reordered and hash(frozen) == hash(reordered)
    assert thaw(frozen) == NESTED
    assert frozen["b"] == (1, JsonObject({"x": None, "y": (True, 2.5)}))


def test_json_object_is_a_read_only_mapping():
    obj = JsonObject({"b": 2, "a": 1})
    assert list(obj) == ["a", "b"] and len(obj) == 2
    assert list(obj.keys()) == ["a", "b"] and list(obj.values()) == [1, 2]
    assert list(obj.items()) == [("a", 1), ("b", 2)] and dict(obj) == {"a": 1, "b": 2}
    assert obj.pairs == (("a", 1), ("b", 2))
    with pytest.raises(TypeError):
        obj["c"] = 3
    assert not JsonObject() and JsonObject() == freeze({})


def test_freeze_is_idempotent():
    frozen = freeze(NESTED)
    assert freeze(frozen) is frozen or freeze(frozen) == frozen
    assert JsonObject(frozen) == frozen
    assert freeze((1, (2, "x"))) == (1, (2, "x"))


@pytest.mark.parametrize("bad", [
    float("nan"), float("inf"), {1: "x"}, {"a": {1, 2}}, object(), b"bytes", {"a": [float("-inf")]},
])
def test_freeze_refuses_non_json_values(bad):
    with pytest.raises(JsonValueError) as caught:
        freeze(bad)
    assert caught.value.reason == "invalid_field"
    assert isinstance(caught.value, ValueError)


def test_duplicate_keys_in_pairs_are_refused():
    with pytest.raises(JsonValueError):
        JsonObject([("a", 1), ("a", 2)])
