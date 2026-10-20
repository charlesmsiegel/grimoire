"""Package assembly must preserve routes and the scene subsystem's log identity.

The ordered route table is captured independently by the broader compatibility
suite. These cases exercise the assembly's failure boundary too: an incomplete
or duplicate roster must be refused before a caller can observe a partial sort.
"""

import copy
import importlib

import pytest

from grimoire.routes import scenes
from grimoire.store import logs


@pytest.mark.parametrize("package", ["scenes", "character_turns", "runs", "config",
                                     "characters", "ledger", "todo", "tracker", "worlds"])
def test_route_finalization_reorders_the_same_list_and_objects(monkeypatch, package):
    owner = importlib.import_module(f"grimoire.routes.{package}._router")
    expected = list(owner.router.routes)
    shuffled = list(reversed(expected))
    monkeypatch.setattr(owner.router, "routes", shuffled)

    owner.finalize()

    assert owner.router.routes is shuffled
    assert [id(route) for route in shuffled] == [id(route) for route in expected]


@pytest.mark.parametrize("package", ["scenes", "character_turns", "runs", "config",
                                     "characters", "ledger", "todo", "tracker", "worlds"])
@pytest.mark.parametrize("fault", ["missing", "extra", "duplicate"])
def test_incomplete_route_rosters_are_refused_before_reordering(monkeypatch, package, fault):
    owner = importlib.import_module(f"grimoire.routes.{package}._router")
    incomplete = list(reversed(owner.router.routes))
    if fault == "missing":
        incomplete.pop()
    elif fault == "extra":
        extra = copy.copy(incomplete[0])
        extra.path = "/package-assembly-probe"
        incomplete.append(extra)
    else:
        incomplete.append(incomplete[0])
    before = [id(route) for route in incomplete]
    monkeypatch.setattr(owner.router, "routes", incomplete)

    with pytest.raises(RuntimeError, match=r"duplicate route key|route roster moved"):
        owner.finalize()

    assert owner.router.routes is incomplete
    assert [id(route) for route in incomplete] == before


def test_identity_phase_keeps_its_persisted_log_module(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    parsed = {"summary": ""}
    block = {"status": "skipped", "matching": "lexical"}

    assert scenes._identity_outcome("saltmarch", "scene", None, parsed, block) is parsed

    rows = logs.read(module="routes.scenes", contains="continuity identity check")["rows"]
    assert [(row["module"], row["message"]) for row in rows] == [
        ("routes.scenes", "continuity identity check")]
