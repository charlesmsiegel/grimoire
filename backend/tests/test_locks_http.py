"""Contention reaches the user as a 409, and never as a hang, a silent skip,
or a mislabelled error (#234)."""

import pytest
from fastapi.testclient import TestClient

from grimoire import main, store
from grimoire.store import campaigns, locks, worlds


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return TestClient(main.create_app())


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid, module="pool-basic")


def test_store_busy_becomes_a_409(client, monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)

    def busy(*a, **k):
        raise locks.CampaignBusy(cid)

    monkeypatch.setattr(store.scenes, "create_scene", busy)
    r = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S1"})
    assert r.status_code == 409
    assert "another grimoire process" in r.json()["detail"]


def test_module_edit_busy_becomes_a_409_too(client, monkeypatch, tmp_path):
    """The handler is registered on the StoreBusy base, not on CampaignBusy,
    so the module-edit domain gets the same treatment."""
    _campaign(monkeypatch, tmp_path)

    def busy(*a, **k):
        raise locks.ModuleEditBusy()

    monkeypatch.setattr(store.module_edit, "create_module", busy)
    r = client.post("/api/modules", json={"name": "Whatever"})
    assert r.status_code == 409
    assert "another grimoire process" in r.json()["detail"]


def test_startup_survives_a_busy_recover(monkeypatch, tmp_path):
    """A second backend starting while the first is mid-edit must serve, not
    refuse to start: recovery is idempotent and the holder is already doing it."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))

    def busy():
        raise locks.ModuleEditBusy()

    monkeypatch.setattr(main.module_edit, "recover", busy)
    with TestClient(main.create_app()) as c:
        assert c.get("/api/config").status_code == 200


def test_capture_baseline_propagates_contention(monkeypatch, tmp_path):
    """capture_baseline swallows every Exception so a capture failure cannot
    fail scene creation. Contention is the one exception: a scene that quietly
    cannot be audited is not recoverable, a 409 is.

    Safe because its only caller, scenes._create_scene, already holds the
    campaign lock (reentrantly) -- so this re-raise guards a future caller that
    does not, rather than a live path.
    """
    from grimoire.store import audit

    cid = _campaign(monkeypatch, tmp_path)

    def busy(c):
        raise locks.CampaignBusy(c)

    monkeypatch.setattr(audit.locks, "campaign_lock", busy)
    with pytest.raises(locks.CampaignBusy):
        audit.capture_baseline(cid, "0001-x")
