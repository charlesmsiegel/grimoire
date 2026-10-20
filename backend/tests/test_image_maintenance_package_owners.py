"""Image maintenance packages retain pinned run state and public identities."""

import importlib
import typing

import pytest


@pytest.mark.parametrize("name, owner, bindings", [
    ("image_migration", "_execution_state", ("_Exec", "pending_ids")),
    ("image_migration", "_contracts", ("DONE", "CANCELLED", "FAILED", "ROOT_CHANGED",
                                         "_KIND", "_RELATION")),
    ("image_gc", "_collection", ("_Run", "collect")),
    ("image_gc", "_tokens", ("_claim_token", "_issue_token")),
])
def test_maintenance_bindings_share_one_defining_owner(name, owner, bindings):
    package = importlib.import_module(f"grimoire.store.{name}")
    assert hasattr(package, "__path__"), f"{name} must be an importable package"
    implementation = importlib.import_module(f"grimoire.store.{name}.{owner}")
    for binding in bindings:
        assert getattr(package, binding) is getattr(implementation, binding)


def test_gc_keeps_its_original_logger_name():
    package = importlib.import_module("grimoire.store.image_gc")
    assert package._log.name == "grimoire.store.image_gc"


@pytest.mark.parametrize("name", ["image_migration", "image_gc"])
def test_maintenance_module_annotations_resolve(name):
    package = importlib.import_module(f"grimoire.store.{name}")
    assert set(typing.get_type_hints(package)) == set(package.__annotations__)
