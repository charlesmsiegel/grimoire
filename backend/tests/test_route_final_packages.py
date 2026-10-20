"""Derived catalogs and tracker locks retain their original ownership lifetime."""

from types import SimpleNamespace

from grimoire.routes import todo, tracker
from grimoire.routes.todo import _catalog, _context
from grimoire.routes.tracker import _locks


def test_todo_catalog_aliases_keep_the_actual_tables_and_builder_functions():
    assert todo._Ctx is _context._Ctx
    assert todo.ITEMS is _catalog.ITEMS
    assert todo.BUILDERS is _catalog.BUILDERS
    assert todo.CAMPAIGN_BUILDERS is _catalog.CAMPAIGN_BUILDERS
    assert todo.LIBRARY_BUILDERS is _catalog.LIBRARY_BUILDERS
    for _name, builder in todo.BUILDERS:
        assert builder is getattr(todo, builder.__name__)
    for expand in todo.ITEMS.values():
        assert expand is getattr(todo, expand.__name__)


def test_tracker_scene_locks_remain_per_app_campaign_and_scene_identity():
    first = SimpleNamespace(state=SimpleNamespace())
    second = SimpleNamespace(state=SimpleNamespace())

    lock = tracker._scene_lock(first, "saltmarch", "scene")

    assert tracker._LOCKS_GUARD is _locks._LOCKS_GUARD
    assert tracker._scene_lock(first, "saltmarch", "scene") is lock
    assert tracker._scene_lock(first, "saltmarch-two", "scene") is not lock
    assert tracker._scene_lock(first, "saltmarch", "scene-two") is not lock
    assert tracker._scene_lock(second, "saltmarch", "scene") is not lock
