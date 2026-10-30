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
    assert freeze(frozen) is frozen
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


def test_equality_is_type_strict_at_every_depth():
    assert JsonObject({"v": 1}) != JsonObject({"v": True})
    assert JsonObject({"v": 1}) != JsonObject({"v": 1.0})
    assert JsonObject({"v": [1, {"w": 0}]}) != JsonObject({"v": [1, {"w": False}]})
    assert len({JsonObject({"v": 1}), JsonObject({"v": True}), JsonObject({"v": 1.0})}) == 3
    assert JsonObject({"v": [1, 2]}) == JsonObject({"v": (1, 2)})


def test_a_json_object_equals_an_equivalent_plain_mapping():
    assert JsonObject({"a": [1]}) == {"a": [1]} and {"a": [1]} == JsonObject({"a": [1]})
    assert JsonObject({"a": 1}) != {"a": True} and JsonObject({"a": 1}) != {"a": 1, "b": 2}
    assert JsonObject({"a": 1}) != {1: "a"} and JsonObject() != [] and JsonObject() != "x"


def test_builtin_subclasses_are_stored_as_plain_json_types():
    import enum

    class Level(enum.IntEnum):
        HIGH = 3

    class Label(str):
        pass

    frozen = freeze({"level": Level.HIGH, "label": Label("x")})
    assert type(frozen["level"]) is int and type(frozen["label"]) is str


@pytest.mark.parametrize("bad", [5, None, "ab"])
def test_a_json_object_needs_a_mapping_or_pairs(bad):
    with pytest.raises(JsonValueError):
        JsonObject(bad)


def test_subclass_overrides_cannot_change_stored_values():
    class Liar(int):
        def __int__(self):
            return 99

    class Key(str):
        pass

    frozen = freeze({Key("k"): Liar(3)})
    assert frozen["k"] == 3 and type(frozen["k"]) is int and type(next(iter(frozen))) is str


def test_negative_zero_is_kept_apart():
    assert JsonObject({"v": -0.0}) != JsonObject({"v": 0.0})
