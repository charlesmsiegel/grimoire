"""Package extraction keeps shared store state and captured defaults intact."""

import importlib
import inspect
import typing

import pytest


@pytest.mark.parametrize("name, owner, binding", [
    ("continuity.reconcile", "_discovery", "EmbedBudget"),
    ("inference.resolve", "_contracts", "NO_SAMPLING"),
    ("inference.migrate", "_tracking", "_active_guard"),
    ("inference.migrate", "_tracking", "_active"),
    ("inference.migrate", "_tracking", "_queued"),
    ("inference.legacy_plan", "_planning", "Overlay"),
    ("context.epistemic", "_classification", "_READ"),
    ("absorb.materializer", "_record_ids", "assign_ids"),
])
def test_shared_store_bindings_have_one_defining_owner(name, owner, binding):
    package = importlib.import_module(f"grimoire.store.{name}")
    implementation = importlib.import_module(f"grimoire.store.{name}.{owner}")
    assert getattr(package, binding) is getattr(implementation, binding)


def test_narrator_default_uses_the_same_read_sentinel():
    package = importlib.import_module("grimoire.store.context.epistemic")
    captured = inspect.signature(package.narrator_view).parameters["identity"].default
    assert captured is package._READ


@pytest.mark.parametrize("name", ["inference.migrate", "context.epistemic"])
def test_loggers_keep_the_original_logical_module(name):
    package = importlib.import_module(f"grimoire.store.{name}")
    assert package.log.name == f"grimoire.store.{name}"


@pytest.mark.parametrize("name, expected", [
    ("continuity.reconcile", {"DECISIONS": "dict[str, tuple[str, ...]]",
                              "LABELS": "dict[str, str]"}),
    ("inference.resolve", {"OPERATION_CAPABILITY": "dict[str, tuple[str, ...]]"}),
    ("inference.migrate", {"STATES": "tuple[str, ...]",
                            "OWNED_GLOBAL_KEYS": "tuple[str, ...]",
                            "_active": "dict[str, dict | None]", "_queued": "set[str]"}),
    ("inference.legacy_plan", {"REPRESENTABLE": "tuple[str, ...]",
                                "OWNED_GLOBAL_KEYS": "tuple[str, ...]"}),
    ("context.epistemic", {}),
    ("absorb.materializer", {}),
])
def test_original_module_annotations_remain_resolvable(name, expected):
    package = importlib.import_module(f"grimoire.store.{name}")
    assert getattr(package, "__annotations__", {}) == expected
    assert set(typing.get_type_hints(package)) == set(expected)
