import pytest

from grimoire.store import climates

EXPECTED = {"temperate-interior", "temperate-coastal", "high-desert",
            "monsoon", "boreal", "equatorial"}


def test_all_documented_presets_ship(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert {c["id"] for c in climates.list_climates()} == EXPECTED


@pytest.mark.parametrize("cid", sorted(EXPECTED))
def test_every_preset_loads_and_validates(monkeypatch, tmp_path, cid):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert climates.get(cid) is not None


def test_presets_have_climatically_varied_season_counts(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    counts = {c: len(climates.get(c)["seasons"]) for c in EXPECTED}
    assert counts["equatorial"] == 1
    assert counts["monsoon"] == 2
    assert counts["temperate-interior"] == 4


def test_every_preset_resolves_weather_all_year(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store.weather.draw import draw
    from grimoire.store.weather.seasons import season_for
    for cid in EXPECTED:
        climate = climates.get(cid)
        for step in range(100):
            season = season_for(climate, step / 100)
            got = draw("realm", "saltmarch", season, climate["persistence"], step)
            assert got["condition"] and got["temperature"] and got["wind"]


def test_every_preset_id_matches_its_filename(monkeypatch, tmp_path):
    # The registry keys on the id inside the document, so a mismatched filename
    # would ship a preset nobody can address by the name they see.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    import json
    import pathlib

    from grimoire.store import climates as reg
    presets = pathlib.Path(reg.__file__).parent / "presets"
    for path in presets.glob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["id"] == path.stem


def test_constrained_conditions_never_escape_their_band(monkeypatch, tmp_path):
    # boreal's blizzard requires `bitter`, its snow requires bitter or freezing.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store.weather.draw import draw
    climate = climates.get("boreal")
    winter = climate["seasons"][0]
    for i in range(3000):
        got = draw("realm", "saltmarch", winter, climate["persistence"], i)
        if got["condition"] == "blizzard":
            assert got["temperature"] == "bitter"
        if got["condition"] == "snow":
            assert got["temperature"] in {"bitter", "freezing"}
