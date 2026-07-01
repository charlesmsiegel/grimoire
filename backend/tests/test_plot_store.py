from grimoire.store import campaigns, plot, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def test_read_missing_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert plot.read(cid) == {}


def test_set_movement_creates_and_appends_beats(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    plot.set_movement(cid, "the-map", "The forged map", "open", "Elara got the map.", "s10")
    plot.set_movement(cid, "the-map", "", "advanced", "It's a forgery.", "s12")
    t = plot.get(cid, "the-map")
    assert t["title"] == "The forged map"      # preserved when passed blank
    assert t["status"] == "advanced"
    assert [b["text"] for b in t["beats"]] == ["Elara got the map.", "It's a forgery."]
    assert [b["scene"] for b in t["beats"]] == ["s10", "s12"]
    assert t["last_scene"] == "s12"


def test_set_movement_empty_beat_does_not_append(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    plot.set_movement(cid, "the-map", "The map", "open", "First.", "s1")
    plot.set_movement(cid, "the-map", "The map", "closed", "", "s2")  # no beat
    t = plot.get(cid, "the-map")
    assert t["status"] == "closed" and t["last_scene"] == "s2"
    assert len(t["beats"]) == 1


def test_open_threads_excludes_closed_and_sorts(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    plot.set_movement(cid, "b", "Bee", "open", "beat b", "s2")
    plot.set_movement(cid, "a", "Ay", "advanced", "beat a", "s1")
    plot.set_movement(cid, "z", "Zed", "closed", "done", "s3")
    got = plot.open_threads(cid)
    assert [t["id"] for t in got] == ["a", "b"]  # closed 'z' gone; sorted by last_scene
    assert got[0] == {"id": "a", "title": "Ay", "status": "advanced", "latest_beat": "beat a"}
