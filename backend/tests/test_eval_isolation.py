"""Temporary eval libraries release cache handles before removal on every exit."""

import contextlib
import os
import sqlite3

import pytest

from evals.epistemic import runner as epistemic_runner
from evals.history import runner as history_runner
from evals.investigation import runner as investigation_runner
from grimoire.store import paths
from tests import cache_sync_fixtures as fx

cache, upper = fx.cache, fx.upper


@pytest.mark.parametrize("isolate", [history_runner.isolated, investigation_runner.isolated,
                                     epistemic_runner._home], ids=["history", "investigation", "epistemic"])
@pytest.mark.parametrize("interrupted", [False, True], ids=["normal", "error"])
@pytest.mark.parametrize("previous_set", [False, True], ids=["no-previous-root", "previous-root"])
def test_eval_home_closes_cache_and_restores_previous_root(isolate, interrupted, previous_set,
                                                          cache, upper, monkeypatch, tmp_path):
    previous = tmp_path / "previous"
    if previous_set:
        monkeypatch.setenv("GRIMOIRE_HOME", str(previous))
    else:
        monkeypatch.delenv("GRIMOIRE_HOME", raising=False)
    expected = pytest.raises(RuntimeError, match="fixture interruption") if interrupted else contextlib.nullcontext()
    with expected, isolate():
        home = paths.home()
        fx._write(home, "worlds/realm/lore/mara.md", "Mara")
        assert fx._read(home, upper, "worlds/realm/lore/mara.md") == "MARA"
        connection = cache._state.session.conn
        assert connection.execute("SELECT 1").fetchone() == (1,)
        if interrupted:
            raise RuntimeError("fixture interruption")
    assert os.environ.get("GRIMOIRE_HOME") == (str(previous) if previous_set else None)
    assert not home.exists()
    # Linux allows unlinking an open SQLite file; checking the actual handle
    # pins resource release there too, rather than relying on Windows refusal.
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
