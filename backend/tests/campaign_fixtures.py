"""A bare campaign over a throwaway store, shared by the store suites that need
nothing more under them.

Each is a world "Realm" holding one campaign, created through the store under
this test's own `GRIMOIRE_HOME` (function-scoped, like the monkeypatch that
sets it). They differ only in the campaign's name, which some suites see in
titles and slugs, and in the fixture name a suite's tests take. A suite
imports the one it wants by name, as itself (`from tests.campaign_fixtures
import run_cid as run_cid`), the way `history_fixtures.cid` is imported: the
two `cid` fixtures are registered under `name="cid"`, so the import's own
name never collides with the tests' `cid` argument.
"""

import pytest

from grimoire.store import campaigns, worlds


def _bare_campaign(monkeypatch, tmp_path, name: str) -> str:
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign(name, wid)


@pytest.fixture
def plain_campaign(monkeypatch, tmp_path):
    """Campaign "Run" in world "Realm"."""
    return _bare_campaign(monkeypatch, tmp_path, "Run")


@pytest.fixture(name="cid")
def run_cid(monkeypatch, tmp_path):
    """`cid`: campaign "Run" in world "Realm"."""
    return _bare_campaign(monkeypatch, tmp_path, "Run")


@pytest.fixture(name="cid")
def saltmarch_cid(monkeypatch, tmp_path):
    """`cid`: campaign "Saltmarch" in world "Realm"."""
    return _bare_campaign(monkeypatch, tmp_path, "Saltmarch")
