"""`home` fixtures shared by suites that need a throwaway store root and one
more thing about it. Each is registered under `name="home"`, so a suite
imports the one it wants as itself (`from tests.home_fixtures import
logs_home as logs_home`) and its tests keep taking `home`. Both are
function-scoped, like the monkeypatch that sets `GRIMOIRE_HOME`."""

import importlib

import pytest

import grimoire.store as store
from grimoire.store import logs


@pytest.fixture(name="home")
def logs_home(monkeypatch, tmp_path):
    """`GRIMOIRE_HOME` at this test's tmp_path, with the log module's state
    reset on both sides of the test: the threshold and the per-file byte
    counters outlive one test's store, and a count left over from the previous
    tmp_path would be charged against a file this one has not written yet."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    logs.forget_file_sizes()
    logs.apply_level("info")
    yield tmp_path
    logs.forget_file_sizes()
    logs.apply_level("info")


@pytest.fixture(name="home")
def reloaded_home(monkeypatch, tmp_path):
    """`GRIMOIRE_HOME` at this test's tmp_path, with `grimoire.store`
    reloaded against it."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return tmp_path
