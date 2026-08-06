"""First-run detection for the setup wizard (#194).

The route's answer decides whether the frontend hijacks `/` and sends the user
into a wizard, so these tests are as interested in the cases that must answer
*False* as in the one that must answer True.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store import paths


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return TestClient(create_app())


def _world(client, name="Realm"):
    return client.post("/api/worlds", json={"name": name}).json()["id"]


# ---- the cheap emptiness checks ----
def test_has_worlds_and_has_campaigns_start_false(client):
    assert store.worlds.has_worlds() is False
    assert store.campaigns.has_campaigns() is False


def test_has_worlds_agrees_with_the_listing(client):
    wid = _world(client)
    assert store.worlds.has_worlds() is True
    assert [w["id"] for w in store.worlds.list_worlds()] == [wid]


def test_has_campaigns_agrees_with_the_listing(client):
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    assert store.campaigns.has_campaigns() is True
    assert [c["id"] for c in store.campaigns.list_campaigns()] == [cid]


def test_a_directory_the_listing_skips_is_not_a_world(client, tmp_path):
    # No world.md: `list_worlds` skips it, so the emptiness check must too --
    # a store holding only stray directories has not been set up.
    (tmp_path / "worlds" / "leftover").mkdir(parents=True)
    assert store.worlds.has_worlds() is False
    assert store.worlds.list_worlds() == []


def test_a_directory_named_by_an_unusable_id_is_not_a_world(client, tmp_path):
    # `safe_id` rejects the trailing dot, so `list_worlds` refuses to hand this
    # id back; counting it as content would report an unopenable store as set up.
    stray = tmp_path / "worlds" / "realm."
    stray.mkdir(parents=True)
    (stray / "world.md").write_text("---\nname: Realm\n---\n", encoding="utf-8")
    assert store.worlds.has_worlds() is False
    assert store.worlds.list_worlds() == []


def test_any_child_record_treats_a_missing_directory_as_empty(tmp_path):
    assert paths.any_child_record(tmp_path / "never-created", "world.md") is False


def test_a_record_that_cannot_be_probed_is_not_reported_as_absent(tmp_path, monkeypatch):
    """The failure direction that matters: a library we could not read must not
    come back as an empty store, because empty is what opens the wizard."""
    (tmp_path / "worlds" / "realm").mkdir(parents=True)

    def denied(entry, meta_name):
        raise PermissionError(entry)

    monkeypatch.setattr(paths, "_names_a_record", denied)
    with pytest.raises(OSError):
        paths.any_child_record(tmp_path / "worlds", "world.md")


# ---- the route ----
def test_a_fresh_install_reports_first_run(client):
    body = client.get("/api/config").json()
    assert body["first_run"] is True
    assert body["setup_done"] == "off"


def test_a_store_with_a_world_is_not_first_run_and_records_it(client):
    _world(client)
    body = client.get("/api/config").json()
    assert body["first_run"] is False
    # backfilled, so the scan never has to run again for this store
    assert store.read_config()["setup_done"] == "on"
    # and the response that did the backfilling reports it, rather than the
    # pre-write value it was handed
    assert body["setup_done"] == "on"


def test_a_write_that_triggers_the_backfill_reports_it(client):
    # PUT hands _public_config the config as it was *before* the backfill.
    _world(client)
    body = client.put("/api/config", json={"theme": "manuscript"}).json()
    assert body["setup_done"] == "on"
    assert body["first_run"] is False


def test_a_store_with_only_a_campaign_is_not_first_run(client, tmp_path):
    # A campaign with no world of its own -- the check is an OR, not an AND.
    root = tmp_path / "campaigns" / "saltmarch"
    root.mkdir(parents=True)
    (root / "campaign.md").write_text("---\nname: Saltmarch\n---\n", encoding="utf-8")
    assert client.get("/api/config").json()["first_run"] is False


def test_dismissing_the_wizard_sticks_on_an_empty_store(client):
    assert client.get("/api/config").json()["first_run"] is True
    body = client.put("/api/config", json={"setup_done": "on"}).json()
    assert body["setup_done"] == "on"
    assert body["first_run"] is False
    # still empty, and still not first run: the flag is the authority
    assert store.worlds.has_worlds() is False
    assert client.get("/api/config").json()["first_run"] is False


def test_emptying_the_library_does_not_bring_the_wizard_back(client):
    wid = _world(client)
    client.get("/api/config")                      # backfills setup_done
    assert client.delete(f"/api/worlds/{wid}").status_code == 200
    assert store.worlds.has_worlds() is False
    assert client.get("/api/config").json()["first_run"] is False


def test_a_store_that_cannot_be_scanned_is_not_reported_as_first_run(client, monkeypatch):
    def boom():
        raise OSError("permission denied")

    monkeypatch.setattr(store.worlds, "has_worlds", boom)
    body = client.get("/api/config").json()
    assert body["first_run"] is False
    assert body["setup_done"] == "off"             # nothing was recorded on a guess


def test_the_backfill_failing_does_not_break_the_config_read(client, monkeypatch):
    _world(client)

    def boom(**fields):
        raise OSError("read-only store")

    monkeypatch.setattr(store, "write_config", boom)
    assert client.get("/api/config").json()["first_run"] is False


def test_setup_done_survives_an_unrelated_config_write(client):
    client.put("/api/config", json={"setup_done": "on"})
    client.put("/api/config", json={"theme": "manuscript"})
    assert client.get("/api/config").json()["setup_done"] == "on"
