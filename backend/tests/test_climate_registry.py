import json

from grimoire.store import climates


def write_custom(tmp_path, cid, **over):
    doc = {
        "id": cid, "name": cid.title(), "persistence": 0.5,
        "seasons": [{
            "name": "all year", "from": 0.0, "to": 0.0,
            "temperature": [{"name": "mild", "weight": 1}],
            "conditions": [{"name": "clear", "weight": 1}],
            "wind": [{"name": "calm", "weight": 1}],
        }],
    }
    doc.update(over)
    d = tmp_path / "climates"
    d.mkdir(exist_ok=True)
    (d / f"{cid}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_shipped_fallback_is_present(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert climates.get(climates.FALLBACK_ID) is not None


def test_unknown_id_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert climates.get("no-such-climate") is None


def test_custom_climate_is_listed_and_loadable(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    write_custom(tmp_path, "saltmarch-fens")
    listed = {c["id"]: c for c in climates.list_climates()}
    assert listed["saltmarch-fens"] == {
        "id": "saltmarch-fens", "name": "Saltmarch-Fens", "builtin": False, "custom": True}


def test_custom_shadows_builtin_and_both_flags_are_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    write_custom(tmp_path, climates.FALLBACK_ID, name="Mine")
    listed = {c["id"]: c for c in climates.list_climates()}
    assert listed[climates.FALLBACK_ID]["builtin"] is True
    assert listed[climates.FALLBACK_ID]["custom"] is True
    assert climates.get(climates.FALLBACK_ID)["name"] == "Mine"


def test_malformed_custom_file_is_skipped_not_fatal(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    d = tmp_path / "climates"
    d.mkdir()
    (d / "broken.json").write_text("{not json", encoding="utf-8")
    write_custom(tmp_path, "highreach-scarp")
    ids = {c["id"] for c in climates.list_climates()}
    assert "highreach-scarp" in ids and "broken" not in ids


def test_structurally_wrong_custom_file_is_skipped_not_fatal(monkeypatch, tmp_path):
    # Valid JSON, wrong shape. validate() raises ClimateError for the cases it
    # anticipates, but a scan must survive whatever else a hand edit produces.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    d = tmp_path / "climates"
    d.mkdir()
    (d / "arrayish.json").write_text("[1, 2, 3]", encoding="utf-8")
    write_custom(tmp_path, "highreach-scarp")
    assert {c["id"] for c in climates.list_climates()} >= {"highreach-scarp"}


def test_invalid_custom_climate_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    write_custom(tmp_path, "bad-one", seasons=[{
        "name": "x", "from": 0.0, "to": 0.0,
        "temperature": [{"name": "mild", "weight": 0}],
        "conditions": [{"name": "clear", "weight": 1}],
        "wind": [{"name": "calm", "weight": 1}]}])
    assert climates.get("bad-one") is None


def test_climate_with_unsafe_id_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    write_custom(tmp_path, "fine-one")
    d = tmp_path / "climates"
    (d / "slashy.json").write_text(
        json.dumps({"id": "a/b", "name": "x", "seasons": []}), encoding="utf-8")
    assert climates.get("a/b") is None


def test_edits_are_seen_without_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert climates.get("late-arrival") is None
    write_custom(tmp_path, "late-arrival")
    assert climates.get("late-arrival") is not None
