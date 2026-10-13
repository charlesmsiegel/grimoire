"""Shared fixtures for the spec 05 cache-sync suites.

A `tmp_path` store with the placeholder names (Realm, Saltmarch, Mara), the
compiled cache on with a fresh process state per test, and a single-file kind
registered for the test -- 03 wired no per-file kind of its own.
"""

from __future__ import annotations

import pytest

from grimoire.store import cache_sync, compiled, maintenance_reports

SENTINEL = "SENTINEL-Seraphine-7f3a"


@pytest.fixture
def cache(monkeypatch):
    monkeypatch.setattr(compiled, "_state", compiled._State())
    monkeypatch.setattr(compiled, "_build", lambda: "b" * 64)
    monkeypatch.setattr(maintenance_reports, "device_key", lambda root: "a" * 64)
    monkeypatch.delenv("GRIMOIRE_COMPILED_CACHE", raising=False)
    monkeypatch.setattr(cache_sync, "_warned", set())
    yield compiled
    compiled.close()


@pytest.fixture
def home(tmp_path, monkeypatch, cache):
    root = tmp_path / "home"
    for d in ("worlds/realm/lore", "campaigns/saltmarch", "logs", "llm_connections", ".cache"):
        (root / d).mkdir(parents=True)
    monkeypatch.setenv("GRIMOIRE_HOME", str(root))
    return root


def _upper(view, params):
    return view.files[0].data.decode("utf-8").upper()


@pytest.fixture
def upper(monkeypatch, cache):
    """A single-file kind: the upper-cased body."""
    kind = compiled.Kind("test:upper", 1, (), (), True)
    monkeypatch.setitem(compiled.KINDS, kind.name, kind)
    recipe = cache_sync.FileRecipe(kind, "body", lambda path, instance: {}, _upper)
    monkeypatch.setitem(cache_sync.FILE_RECIPES, kind.name, recipe)
    return recipe


def _read(home, recipe, rel):
    """What a reader does: derive through the recipe, which records the row."""
    with compiled.batch(home) as request:
        return cache_sync.derive_file(request, recipe, home / rel)


def _write(home, rel, text):
    path = home / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _by_path(report):
    return {p.given: p for p in report.paths}

