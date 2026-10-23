"""Missing moods cannot silently change a character's clothes."""

from grimoire.store.expression_library import read, resolve
from tests.test_expressions_store import apply, outfit, png
from tests.test_expressions_store import target as target


def test_armor_neutral_beats_everyday_anger(target):
    outfit(target, "armor")
    outfit(target, "everyday")
    apply(target, {"op": "put-expression", "outfit": "everyday", "expression": "anger", "data": png(), "ext": "png"},
          {"op": "set-default", "outfit": "everyday"})
    choice = resolve.sprite(read.effective(target), "armor", "anger")
    assert (choice.outfit_id, choice.expression, choice.fallback) == ("armor", "neutral", "outfit")


def test_hidden_neutral_and_sparse_custom_fallback(target):
    outfit(target, "armor")
    apply(target, {"op": "put-expression", "outfit": "armor", "expression": "stern", "data": png(), "ext": "png"},
          {"op": "hide-expression", "outfit": "armor", "expression": "neutral"},
          {"op": "patch-outfit", "outfit": "armor", "metadata": {"fallback_expression": "stern"}})
    assert resolve.sprite(read.effective(target), "armor", "anger").expression == "stern"


def test_unreadable_selected_outfit_tries_same_outfit_before_default(target):
    outfit(target, "armor")
    outfit(target, "everyday")
    apply(target, {"op": "put-expression", "outfit": "armor", "expression": "anger", "data": png(), "ext": "png"},
          {"op": "set-default", "outfit": "everyday"})
    choices = resolve.candidates(read.effective(target), "armor", "anger")
    assert [(c.outfit_id, c.expression) for c in choices[:3]] == [
        ("armor", "anger"), ("armor", "neutral"), ("everyday", "neutral")]
    assert choices[-1].fallback == "placeholder"


def test_empty_selected_outfit_uses_default_then_avatar(target):
    outfit(target)
    apply(target, {"op": "create-outfit", "outfit": "armor"}, {"op": "set-default", "outfit": "everyday"})
    assert resolve.sprite(read.effective(target), "armor", "anger").fallback == "default-outfit"
    apply(target, {"op": "hide-outfit", "outfit": "everyday"})
    assert resolve.sprite(read.effective(target), "armor", "anger").fallback == "placeholder"


def test_broken_config_is_reported_without_disabling_fallback(target):
    outfit(target, "armor")
    outfit(target, "everyday")
    apply(target, {"op": "set-default", "outfit": "armor"},
          {"op": "hide-outfit", "outfit": "armor"},
          {"op": "patch-outfit", "outfit": "everyday", "metadata": {"fallback_expression": "joy"}})
    collection = read.effective(target)
    assert any("armor" in warning for warning in collection["warnings"])
    assert any("everyday/joy" in warning for warning in collection["warnings"])
    assert resolve.sprite(collection, None, "anger").expression == "neutral"
