"""The warm-up facade exposes one fixed tuple and the same owned state."""

import importlib
import logging

import pytest

from grimoire.store import cache_sync, logs


@pytest.mark.parametrize("leaf, binding", [
    ("_signals", "_lane"),
    ("_signals", "_warned"),
    ("_files", "FILE_RECIPES"),
    ("_vectors", "VECTOR_PROJECTIONS"),
    ("_vectors", "_CLIENT"),
])
def test_exported_warm_state_has_one_owner(leaf, binding):
    assert hasattr(cache_sync, "__path__"), "cache_sync must be an importable package"
    owner = importlib.import_module(f"grimoire.store.cache_sync.{leaf}")
    assert getattr(cache_sync, binding) is getattr(owner, binding)


def test_fixed_hook_tuple_uses_the_exact_owned_instances():
    assert hasattr(cache_sync, "__path__"), "cache_sync must be an importable package"
    engine = importlib.import_module("grimoire.store.cache_sync._engine")
    files = importlib.import_module("grimoire.store.cache_sync._files")
    others = importlib.import_module("grimoire.store.cache_sync._other_hooks")
    vectors = importlib.import_module("grimoire.store.cache_sync._vectors")
    assert cache_sync.HOOKS is engine.HOOKS
    assert engine.HOOKS == (files.FILES, others.OVERVIEW, others.SEARCHDOCS, vectors.VECTORS)


def test_logical_warm_logger_label_is_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    logs.install()
    monkeypatch.setattr(cache_sync._log, "level", logging.WARNING)
    cache_sync._log.warning("Cache sync package ownership probe")
    rows = logs.read(module="store.cache_sync", contains="Cache sync package ownership probe")["rows"]
    assert [(row["module"], row["message"]) for row in rows] == [
        ("store.cache_sync", "Cache sync package ownership probe")]
