"""The owning module for `campaigns/<cid>/climate.json` (#237)."""

import json
import pathlib

import pytest

from grimoire.store import campaign_climate, campaigns, climates, worlds


def make_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch Chronicle", "realm")


def custom_climate(tmp_path, cid="saltmarch-fens"):
    """A private climate, so tests have an id that is not the fallback."""
    (tmp_path / "climates").mkdir(exist_ok=True)
    (tmp_path / "climates" / f"{cid}.json").write_text(json.dumps({
        "id": cid, "name": "Fens", "persistence": 0.2,
        "seasons": [{"name": "all", "from": 0.0, "to": 0.0,
                     "temperature": [{"name": "mild", "weight": 1}],
                     "conditions": [{"name": "clear", "weight": 1}],
                     "wind": [{"name": "calm", "weight": 1}]}]}), encoding="utf-8")
    return cid


def test_write_then_read_round_trips(monkeypatch, tmp_path):
    cid = make_campaign(monkeypatch, tmp_path)
    wanted = custom_climate(tmp_path)
    campaign_climate.write_default(cid, wanted)
    assert campaign_climate.read_default(cid) == wanted


def test_the_on_disk_shape_is_the_documented_one(monkeypatch, tmp_path):
    # The one place the file format is asserted: every other reader and writer
    # goes through this module, so this is the whole schema contract.
    cid = make_campaign(monkeypatch, tmp_path)
    wanted = custom_climate(tmp_path)
    campaign_climate.write_default(cid, wanted)
    written = json.loads(campaign_climate.path(cid).read_text(encoding="utf-8"))
    assert written == {"default_climate": wanted}


def test_an_absent_file_reads_as_unset(monkeypatch, tmp_path):
    # Campaigns created before the weather work have no climate.json at all.
    cid = make_campaign(monkeypatch, tmp_path)
    campaign_climate.path(cid).unlink()
    assert campaign_climate.read_default(cid) is None


@pytest.mark.parametrize("raw, why", [
    ("{not json", "truncated by a hand edit"),
    ("7", "a JSON scalar: `json.loads('7').get` is an AttributeError"),
    ('{"default_climate": ["saltmarch-fens"]}', "a list is truthy but unhashable"),
    ('{"default_climate": ""}', "empty is unset, not an id"),
    ('{"other": "saltmarch-fens"}', "the key is missing"),
])
def test_an_unusable_file_reads_as_unset(monkeypatch, tmp_path, raw, why):
    # This file is hand-editable and the read runs inside prompt assembly, so
    # nothing here may raise -- every malformed shape resolves to "unset".
    cid = make_campaign(monkeypatch, tmp_path)
    campaign_climate.path(cid).write_text(raw, encoding="utf-8")
    assert campaign_climate.read_default(cid) is None, why


def test_resolve_returns_the_named_document(monkeypatch, tmp_path):
    cid = make_campaign(monkeypatch, tmp_path)
    wanted = custom_climate(tmp_path)
    campaign_climate.write_default(cid, wanted)
    assert campaign_climate.resolve_default(cid)["id"] == wanted


def test_resolve_falls_back_when_the_default_is_unset(monkeypatch, tmp_path):
    cid = make_campaign(monkeypatch, tmp_path)
    campaign_climate.path(cid).unlink()
    assert campaign_climate.resolve_default(cid)["id"] == climates.FALLBACK_ID


def test_resolve_falls_back_when_the_default_went_dangling(monkeypatch, tmp_path):
    # A climate deleted after the fact must not break a turn -- the write side
    # is strict so this can only happen out-of-band, but it does happen.
    cid = make_campaign(monkeypatch, tmp_path)
    wanted = custom_climate(tmp_path)
    campaign_climate.write_default(cid, wanted)
    (tmp_path / "climates" / f"{wanted}.json").unlink()
    assert campaign_climate.resolve_default(cid)["id"] == climates.FALLBACK_ID


def test_writing_an_unknown_climate_is_rejected(monkeypatch, tmp_path):
    # Strict where the user is present to be told; lenient where they are not.
    cid = make_campaign(monkeypatch, tmp_path)
    before = campaign_climate.path(cid).read_text(encoding="utf-8")
    with pytest.raises(climates.ClimateError):
        campaign_climate.write_default(cid, "no-such-climate")
    assert campaign_climate.path(cid).read_text(encoding="utf-8") == before


def test_only_the_owning_module_names_the_file():
    """#237: four touchpoints across three layers, no module owning the shape.

    A grep is how a schema change had to be found. This is the same check a
    reviewer would run by hand, run by the build instead.
    """
    import grimoire
    package = pathlib.Path(grimoire.__file__).parent
    owner = package / "store" / "campaign_climate.py"
    offenders = [str(p.relative_to(package)) for p in sorted(package.rglob("*.py"))
                 if p != owner and "climate.json" in p.read_text(encoding="utf-8")]
    assert not offenders, (
        "the file is named outside its owning module — read and write it "
        "through store.campaign_climate, and keep the name itself (comments "
        f"included) there, so a grep for it lands in one file: {offenders}")
