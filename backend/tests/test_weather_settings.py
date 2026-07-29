import json

from grimoire.store import campaign_climate, campaigns, climates, worlds
from grimoire.store.weather import settings


def make_campaign(tmp_path):
    worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch Chronicle", "realm")


def location(cid, name, **fields):
    """Create a campaign location, returning its generated id.

    `create_entity` takes a display *name* and slugifies it into the id, and
    accepts the extra frontmatter fields directly.
    """
    from grimoire.store import entities
    return entities.create_entity(
        campaigns.campaign_root(cid), "locations", name, "A place", fields=fields or None)


def test_untagged_location_uses_the_shipped_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    lid = location(cid, "Saltmarch Docks")
    got = settings.resolve(cid, lid)
    assert got["climate"]["id"] == climates.FALLBACK_ID
    assert got["zone"] == lid
    assert got["persistence"] == got["climate"]["persistence"]


def test_campaign_default_is_used_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    (tmp_path / "climates").mkdir()
    (tmp_path / "climates" / "saltmarch-fens.json").write_text(json.dumps({
        "id": "saltmarch-fens", "name": "Fens", "persistence": 0.2,
        "seasons": [{"name": "all", "from": 0.0, "to": 0.0,
                     "temperature": [{"name": "mild", "weight": 1}],
                     "conditions": [{"name": "clear", "weight": 1}],
                     "wind": [{"name": "calm", "weight": 1}]}]}), encoding="utf-8")
    campaign_climate.write_default(cid, "saltmarch-fens")
    lid = location(cid, "Saltmarch Docks")
    assert settings.resolve(cid, lid)["climate"]["id"] == "saltmarch-fens"


def test_non_string_campaign_default_falls_through_without_raising(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    (campaigns.campaign_root(cid) / "climate.json").write_text(
        json.dumps({"default_climate": ["temperate-interior"]}), encoding="utf-8")
    lid = location(cid, "Saltmarch Docks")
    assert settings.resolve(cid, lid)["climate"]["id"] == climates.FALLBACK_ID


def test_unknown_campaign_default_falls_through_to_the_preset(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    (campaigns.campaign_root(cid) / "climate.json").write_text(
        json.dumps({"default_climate": "deleted-climate"}), encoding="utf-8")
    lid = location(cid, "Saltmarch Docks")
    assert settings.resolve(cid, lid)["climate"]["id"] == climates.FALLBACK_ID


def test_unparseable_campaign_climate_file_falls_through(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    (campaigns.campaign_root(cid) / "climate.json").write_text("{not json", encoding="utf-8")
    assert settings.resolve(cid, None)["climate"]["id"] == climates.FALLBACK_ID


def test_a_json_scalar_climate_file_falls_through(monkeypatch, tmp_path):
    # `json.loads("7").get` is an AttributeError, from inside prompt assembly.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    (campaigns.campaign_root(cid) / "climate.json").write_text("7", encoding="utf-8")
    assert settings.resolve(cid, None)["climate"]["id"] == climates.FALLBACK_ID


def test_unknown_location_climate_falls_back_without_raising(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    lid = location(cid, "Saltmarch Docks", climate="temperate-costal")  # typo
    assert settings.resolve(cid, lid)["climate"]["id"] == climates.FALLBACK_ID


def test_explicit_weather_zone_is_used(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    lid = location(cid, "Saltmarch Docks", weather_zone="saltmarch")
    assert settings.resolve(cid, lid)["zone"] == "saltmarch"


def test_locations_sharing_a_zone_share_their_sky(monkeypatch, tmp_path):
    # The whole point of the field: two locations in one weather zone must
    # resolve to the same seed, or "same zone" means nothing.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    a = location(cid, "Saltmarch Docks", weather_zone="saltmarch")
    b = location(cid, "Saltmarch Market", weather_zone="saltmarch")
    assert settings.resolve(cid, a)["zone"] == settings.resolve(cid, b)["zone"]


def test_location_persistence_overrides_the_climate(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    lid = location(cid, "Saltmarch Docks", persistence="0.3")
    assert settings.resolve(cid, lid)["persistence"] == 0.3


def test_out_of_range_persistence_falls_back_to_the_climate(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    for n, bad in enumerate(("2", "-1", "NaN", "wet")):
        lid = location(cid, f"Winifred Hall {n}", persistence=bad)
        got = settings.resolve(cid, lid)
        assert got["persistence"] == got["climate"]["persistence"], bad


def test_zero_persistence_is_honoured_not_treated_as_unset(monkeypatch, tmp_path):
    # "0" is falsy in every language this project speaks; it is also a legal
    # setting meaning "no correlation between blocks".
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    lid = location(cid, "Saltmarch Docks", persistence="0")
    assert settings.resolve(cid, lid)["persistence"] == 0.0


def test_deleted_location_resolves_from_the_campaign_default(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    got = settings.resolve(cid, "was-deleted")
    assert got["climate"]["id"] == climates.FALLBACK_ID
    assert got["zone"] == "was-deleted"  # the id is still a stable seed


def test_no_location_at_all_resolves_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    assert settings.resolve(cid, None)["climate"]["id"] == climates.FALLBACK_ID
