import json

import pytest

from grimoire.store import campaigns, dice, rolls, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def test_read_missing_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert rolls.read(cid) == []


def test_read_garbled_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "rolls.json").write_text("{not json", encoding="utf-8")
    assert rolls.read(cid) == []
    (campaigns.campaign_root(cid) / "rolls.json").write_text('{"a": 1}', encoding="utf-8")
    assert rolls.read(cid) == []


def test_append_assigns_sequential_ids_and_persists(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    e1 = rolls.append(cid, "s1", "Perception", dice.roll("2d6", seed=1))
    e2 = rolls.append(cid, None, None, dice.roll("d20", seed=2))
    assert e1["id"] == "r1" and e2["id"] == "r2"
    assert e1["scene"] == "s1" and e1["label"] == "Perception" and e1["ts"]
    on_disk = json.loads((campaigns.campaign_root(cid) / "rolls.json").read_text(encoding="utf-8"))
    assert [e["id"] for e in on_disk] == ["r1", "r2"]


def test_get_and_missing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    entry = rolls.append(cid, None, None, dice.roll("2d6", seed=1))
    assert rolls.get(cid, "r1") == entry
    with pytest.raises(rolls.RollNotFound):
        rolls.get(cid, "r99")


def test_replay_matches_stored_result(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    rolls.append(cid, "s1", None, dice.roll("4d6kh3+2 vs 15", seed=42))
    out = rolls.replay(cid, "r1")
    assert out["match"] is True
    assert out["result"] == out["entry"]["result"]


def test_replay_detects_tampering(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    rolls.append(cid, None, None, dice.roll("2d6", seed=3))
    p = campaigns.campaign_root(cid) / "rolls.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data[0]["result"]["total"] = 999
    p.write_text(json.dumps(data), encoding="utf-8")
    assert rolls.replay(cid, "r1")["match"] is False


def test_repoint_scenes_follows_renamed_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    rolls.append(cid, "old-sid", None, dice.roll("2d6", seed=1))
    rolls.repoint_scenes(cid, {"old-sid": "new-sid"})
    assert rolls.read(cid)[0]["scene"] == "new-sid"


def test_repoint_scenes_ignores_unrelated_scenes(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    rolls.append(cid, "other-sid", None, dice.roll("2d6", seed=1))
    rolls.repoint_scenes(cid, {"old-sid": "new-sid"})
    assert rolls.read(cid)[0]["scene"] == "other-sid"
