"""First-run detection for the setup wizard (#194).

The route's answer decides whether the frontend hijacks `/` and sends the user
into a wizard, so these tests are as interested in the cases that must answer
*False* as in the one that must answer True.
"""

import importlib
import threading

import grimoire.store as store
import pytest
from fastapi.testclient import TestClient
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


def test_creating_a_world_records_setup_without_waiting_for_a_config_read(client):
    """Deliberately no config read between the create and the delete: relying on
    one made the guarantee depend on whether a read happened to interleave."""
    wid = _world(client)
    assert store.read_config()["setup_done"] == "on"
    assert client.delete(f"/api/worlds/{wid}").status_code == 200
    assert client.get("/api/config").json()["first_run"] is False


def test_creating_a_campaign_records_setup_too(client):
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    store.write_config(setup_done="off")           # as if only the campaign existed
    client.delete(f"/api/campaigns/{cid}")
    client.delete(f"/api/worlds/{wid}")
    # the campaign's own create already recorded it; re-create to prove the path
    wid2 = _world(client, "Realm")
    client.post("/api/campaigns", json={"name": "Second", "world": wid2})
    assert store.read_config()["setup_done"] == "on"


def test_a_world_survives_a_failed_setup_record(client, monkeypatch):
    """The record happens after the world is on disk, so raising here would fail
    a request whose real work succeeded — and the caller's retry would create a
    uniquely-suffixed duplicate of a world that already exists."""
    def busy(**fields):
        raise store.locks.ConfigBusy()

    # `store.config.write_config`, not `store.write_config`: `mark_setup_done`
    # calls the module-level name, so patching the facade re-export intercepts
    # nothing and the test goes green while injecting no failure at all.
    monkeypatch.setattr(store.config, "write_config", busy)
    r = client.post("/api/worlds", json={"name": "Realm"})
    assert r.status_code == 200
    assert r.json()["id"] == "realm"
    assert [w["id"] for w in client.get("/api/worlds").json()] == ["realm"]


def test_a_campaign_survives_a_failed_setup_record(client, monkeypatch):
    wid = _world(client)
    store.write_config(setup_done="off")

    def boom(**fields):
        raise OSError("read-only store")

    monkeypatch.setattr(store.config, "write_config", boom)   # see above
    r = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid})
    assert r.status_code == 200
    assert store.campaigns.has_campaigns() is True


def test_config_names_the_store_it_describes(client, tmp_path):
    assert client.get("/api/config").json()["data_dir"] == str(tmp_path)


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


def test_two_config_writes_cannot_erase_each_other(client, monkeypatch):
    """`write_config` reads, merges and rewrites the whole file, so without
    serialization two callers merge onto the same pre-image and the second
    publication drops the first's fields.

    This is the store-level statement of the backfill hazard: `_setup_state`
    writes `setup_done` from `GET /api/config`, so the second caller here is
    what a second tab merely *loading the app* does while the first saves a
    setting. Driven through `write_config` directly rather than the routes —
    TestClient serializes requests across threads, so an HTTP-level version of
    this test interleaves nothing and passes with or without the lock.
    """
    store.read_config()                 # materialize config.md first
    real_write = store.config.atomic.write_text
    started = threading.Event()
    release = threading.Event()

    def slow_write(path, text, *a, **kw):
        if path.name == "config.md" and "manuscript" in text:
            started.set()
            release.wait(5)             # hold the theme save between merge and publish
        return real_write(path, text, *a, **kw)

    monkeypatch.setattr(store.config.atomic, "write_text", slow_write)

    saver = threading.Thread(target=lambda: store.write_config(theme="manuscript"))
    saver.start()
    assert started.wait(5)              # the theme save now holds the lock, mid-publish

    backfill = threading.Thread(target=lambda: store.write_config(setup_done="on"))
    backfill.start()
    # Room for the backfill to run all the way through, which is exactly what it
    # does when nothing serializes the two: it merges onto the pre-theme
    # frontmatter and publishes, and the theme save then republishes its own
    # equally stale pre-image over the top. Under the lock this join times out
    # with the backfill still waiting to acquire, which is the point.
    backfill.join(1.0)
    release.set()
    saver.join(10)
    backfill.join(10)

    cfg = store.read_config()
    assert cfg["theme"] == "manuscript"   # neither write erased the other
    assert cfg["setup_done"] == "on"


def test_setup_done_survives_an_unrelated_config_write(client):
    client.put("/api/config", json={"setup_done": "on"})
    client.put("/api/config", json={"theme": "manuscript"})
    assert client.get("/api/config").json()["setup_done"] == "on"


def test_importing_a_world_records_setup_done(client, tmp_path):
    """Arriving by import is arriving (#54). A user whose whole library came
    from a bundle has set this store up just as much as one who typed a name,
    so deleting that world later must not reopen the wizard."""
    wid = _world(client, "Saltmarch")
    blob = client.get(f"/api/worlds/{wid}/export.zip").content
    # Wipe the store's memory of having been set up, so the assertion below can
    # only be satisfied by the import route recording it again.
    client.put("/api/config", json={"setup_done": "off"})

    assert client.post("/api/worlds/import", content=blob,
                       headers={"content-type": "application/zip"}).status_code == 200
    assert store.read_config()["setup_done"] == "on"
