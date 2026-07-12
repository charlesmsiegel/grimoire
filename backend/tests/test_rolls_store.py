import json
import threading

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


def test_append_proposal_tag(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    entry = rolls.append(cid, "s1", "test", dice.roll("1d6", seed=1), proposal="pr-000001")
    assert entry["proposal"] == "pr-000001"
    assert rolls.find_by_proposal(cid, "pr-000001")["id"] == entry["id"]
    assert rolls.find_by_proposal(cid, "pr-999999") is None


def test_append_without_proposal_omits_key(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    entry = rolls.append(cid, "s1", "test", dice.roll("1d6", seed=1))
    assert "proposal" not in entry


def test_find_or_append_by_proposal_appends_once(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    e1 = rolls.find_or_append_by_proposal(cid, "s1", "check", dice.roll("1d6", seed=1), "pr-x")
    e2 = rolls.find_or_append_by_proposal(cid, "s1", "check", dice.roll("1d6", seed=9), "pr-x")
    assert e1["id"] == e2["id"]
    assert len(rolls.read(cid)) == 1


def test_append_concurrent_no_lost_entries(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)

    def worker(i):
        rolls.append(cid, "s1", f"w{i}", dice.roll("1d6", seed=i))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    entries = rolls.read(cid)
    ids = [e["id"] for e in entries]
    assert len(entries) == 8
    assert len(set(ids)) == 8
    assert sorted(ids) == [f"r{i}" for i in range(1, 9)]


def test_find_or_append_by_proposal_concurrent_single_entry(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)

    def worker():
        rolls.find_or_append_by_proposal(cid, "s1", "check", dice.roll("1d6", seed=1), "pr-x")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    tagged = [e for e in rolls.read(cid) if e.get("proposal") == "pr-x"]
    assert len(tagged) == 1


def test_repoint_scenes_concurrent_with_append(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    for i in range(5):
        rolls.append(cid, "old-sid", None, dice.roll("2d6", seed=i))

    appended_ids: list[str] = []

    def appender():
        for i in range(20):
            e = rolls.append(cid, "other-sid", "bg", dice.roll("1d6", seed=i))
            appended_ids.append(e["id"])

    def repointer():
        for _ in range(20):
            rolls.repoint_scenes(cid, {"old-sid": "new-sid"})

    t1 = threading.Thread(target=appender)
    t2 = threading.Thread(target=repointer)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    entries = rolls.read(cid)
    assert len(entries) == 5 + 20
    assert len(set(e["id"] for e in entries)) == len(entries)
    assert all(e.get("scene") != "old-sid" for e in entries)
    assert sum(1 for e in entries if e.get("scene") == "new-sid") == 5
    on_disk_ids = {e["id"] for e in entries}
    assert all(aid in on_disk_ids for aid in appended_ids)
