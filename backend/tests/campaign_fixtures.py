"""A bare campaign over a throwaway store, shared by the store suites that need
nothing more under them -- and `stamp_full_copy`, which turns one back into
the pre-overlay layout.

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
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter


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


def stamp_full_copy(cid: str) -> None:
    """Mark a campaign pre-overlay full-copy -- the state
    `ensure_campaign_slim` migrates from (see campaigns.read.slim_pending), and
    the one where sync.md means the full copy's inventory rather than the set
    of materialized records."""
    mp = campaigns.campaign_meta_path(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["world_copy"] = "full"
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")
