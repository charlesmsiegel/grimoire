"""The scene state tracker's on/off settings: a global default and a campaign
tri-state that wins over it, plus the routes that read and write the latter."""

import pytest

from grimoire import store
from grimoire.store import campaigns, worlds
from grimoire.store.tracker import settings


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch", wid)


def test_tracker_defaults_on_and_campaign_overrides(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert settings.enabled(cid) is True
    campaigns.set_campaign_tracker(cid, "off")
    assert settings.enabled(cid) is False
    # The campaign's own "on" beats a global "off"; clearing it hands the
    # decision back to the global.
    store.write_config(tracker="off")
    campaigns.set_campaign_tracker(cid, "")
    assert settings.enabled(cid) is False
    campaigns.set_campaign_tracker(cid, "on")
    assert settings.enabled(cid) is True


def test_unset_campaign_follows_the_global_setting(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert settings.campaign_setting(cid) == ""
    store.write_config(tracker="off")
    assert settings.enabled(cid) is False
    assert store.config.tracker_enabled() is False


def test_perception_rider_defaults_on(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert store.config.perception_rider() is True
    store.write_config(perception_rider="off")
    assert store.config.perception_rider() is False


def test_a_hand_mangled_campaign_value_counts_as_unset(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    mp = store.campaigns.campaign_meta_path(cid)
    meta, body = store.parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["tracker"] = "maybe"
    mp.write_text(store.dump_frontmatter(meta, body), encoding="utf-8")
    assert settings.campaign_setting(cid) == ""
    assert settings.enabled(cid) is True


def test_bad_campaign_value_rejected(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        campaigns.set_campaign_tracker(cid, "maybe")


def test_clearing_removes_the_key_and_a_noop_write_leaves_updated_alone(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    campaigns.set_campaign_tracker(cid, "off")
    assert campaigns.read_campaign(cid)["meta"]["tracker"] == "off"
    stamp = campaigns.read_campaign(cid)["meta"]["updated"]
    campaigns.set_campaign_tracker(cid, "off")
    assert campaigns.read_campaign(cid)["meta"]["updated"] == stamp
    campaigns.set_campaign_tracker(cid, "")
    assert "tracker" not in campaigns.read_campaign(cid)["meta"]


def _route_campaign(client):
    wid = store.worlds.create_world("Realm")
    return store.campaigns.create_campaign("Saltmarch", wid)


def test_campaign_tracker_routes(client):
    cid = _route_campaign(client)
    r = client.get(f"/api/campaigns/{cid}/tracker")
    assert r.json() == {"setting": "", "enabled": True}
    r = client.put(f"/api/campaigns/{cid}/tracker", json={"setting": "off"})
    assert r.json() == {"setting": "off", "enabled": False}
    assert client.get(f"/api/campaigns/{cid}/tracker").json() == {
        "setting": "off", "enabled": False}
    assert client.put(f"/api/campaigns/{cid}/tracker",
                      json={"setting": "x"}).status_code == 400
    assert client.put(f"/api/campaigns/{cid}/tracker",
                      json={"setting": ""}).json() == {"setting": "", "enabled": True}


def test_campaign_tracker_routes_404_for_an_unknown_campaign(client):
    assert client.get("/api/campaigns/nowhere/tracker").status_code == 404
    assert client.put("/api/campaigns/nowhere/tracker",
                      json={"setting": "on"}).status_code == 404


def test_public_config_carries_tracker_keys(client):
    cfg = client.get("/api/config").json()
    assert cfg["tracker"] == "on" and cfg["perception_rider"] == "on"
    client.put("/api/config", json={"tracker": "off", "perception_rider": "off"})
    cfg = client.get("/api/config").json()
    assert cfg["tracker"] == "off" and cfg["perception_rider"] == "off"
